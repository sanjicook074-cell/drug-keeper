# -*- coding: utf-8 -*-
"""
drug-keeper：药盒（模拟分格药盒 + 拖药进格）—— 真实 DOM 驱动测试。

用户 2026-10-22 的要求，原话：
    「在页面左侧绘制一个模拟的药盒，这个药盒可以选择格数，两格三格四格，想几格就几格。
      然后拖动药品，就可以拖到对应的格子里。因为每天吃的药有饭前吃的、饭后吃的，
      早中晚都不一样，加入这个就能很清楚地看出来药盒里每一格应该放什么药。」

拆成四条硬要求，本脚本就按这四条钉：
  ① **格数可选**：1~7 格，加减即时生效，格名能改（「早饭前」这种）；
  ② **拖得进去**：从下面药品列表拖进格子（拖一次 = 复制一份，早晚各一次的药要拖两次）；
     从格子拖到格子 = 搬过去（整理时不会凭空多一份）；
  ③ **备用药不进盒**：它不按天吃，塞进每日药盒会撒谎；
  ④ **账不串**：删药 / 换人 / 导出导入 / 老存档迁移，四件事都不能让盒子里的 id 变成野指针。

★ 拖拽这一段是真把 PointerEvent 打进页面里跑的，不是直接调 addToSlot() ——
  只调函数只能证明「函数会写数据」，证明不了「鼠标拖过去真能放进去」
  （offset 算错、elementFromPoint 被浮层挡住、pointerup 绑错元素，这些都得靠真事件才暴露）。

做法同 test_prn.py：复制 index.html，在主 IIFE 末尾注入探针，无头 Chrome --dump-dom 取回。
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = drug-keeper/
SRC = os.path.join(ROOT, "index.html")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

PROBE = r"""
/* ===== 注入的测试代码（必须插在主 IIFE 内部，否则 meds / 各函数都看不见）===== */
(function(){
  var R = { alerts: [], cases: [], prompts: [] };
  window.__T = R;
  window.alert = function(m){ R.alerts.push(String(m)); };
  window.confirm = function(){ return true; };
  R.promptValue = null;
  window.prompt = function(m, dflt){
    R.prompts.push(String(m));
    return R.promptValue === null ? dflt : R.promptValue;
  };
  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function agoD(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); }
  function byName(n){
    for(var i = 0; i < meds.length; i++) if(meds[i].name === n) return meds[i];
    return null;
  }
  function card(n){
    var cs = qa('#list .med'), i;
    for(i = 0; i < cs.length; i++){
      var el = cs[i].querySelector('.med-name');
      if(el && el.textContent.indexOf(n) >= 0) return cs[i];
    }
    return null;
  }
  function slots(){ return qa('#box .slot'); }
  function slotNames(){
    return slots().map(function(s){ return s.querySelector('.slot-n').textContent.trim(); });
  }
  function slotIds(i){
    var s = curBox().slots[i];
    return s ? s.ids.slice() : [];
  }
  function nameIn(i, nm){
    var m = byName(nm);
    return !!m && slotIds(i).indexOf(m.id) >= 0;
  }
  function boxText(){ return q('#box').textContent.replace(/\s+/g, ' ').trim(); }

  // 造一批药：3 味每天吃 + 1 味备用；盒子重置成默认 3 格
  function fixture(){
    var p = curPerson();
    p.box = { slots: defaultSlots(3) };
    var list = [
      { name:'早饭前片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
        stockBase:30, stockDate:todayStr(), rxMax:30, kind:'daily' },
      { name:'晚饭后胶囊', spec:'0.25g', perDose:2, unit:'粒', cycleDays:1, timesPerCycle:1,
        stockBase:60, stockDate:todayStr(), rxMax:30, kind:'daily' },
      { name:'一天两次片', spec:'10mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
        stockBase:60, stockDate:todayStr(), rxMax:30, kind:'daily' },
      { name:'疼了才吃片', spec:'0.3g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
        stockBase:8, stockDate:todayStr(), kind:'prn' }
    ];
    list.forEach(function(d){ d.id = uid(); });
    setMeds(list); save(); render();
  }

  /* ---- 真拖拽：把 pointer 事件打进页面，走 dragDown / dragMove / dragUp 全链路 ---- */
  function center(el){
    var r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  }
  function pe(type, x, y, target){
    var ev = new PointerEvent(type, {
      bubbles: true, cancelable: true, clientX: x, clientY: y,
      pointerType: 'mouse', pointerId: 1, button: 0, buttons: 1, isPrimary: true
    });
    (target || document).dispatchEvent(ev);
  }
  // 从「药品列表里的某张卡片」拖到第 i 格；松手点可覆盖（用来测"拖到格子外"）
  function dragToListCard(nm, i, dropAt){
    var c = card(nm);
    if(!c) return 'no-card';
    var s = slots()[i];
    if(!s) return 'no-slot';
    s.scrollIntoView({ block: 'center' });
    var a = center(c.querySelector('.med-name'));
    var b = dropAt || center(s);
    pe('pointerdown', a.x, a.y, c.querySelector('.med-name'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    return 'ok';
  }
  // 从第 i 格的某个药，拖到第 j 格
  function dragChip(i, nm, j){
    var s = slots()[i];
    if(!s) return 'no-slot';
    var m = byName(nm);
    var chip = s.querySelector('.chip[data-med="' + (m ? m.id : '') + '"]');
    if(!chip) return 'no-chip';
    var t = slots()[j];
    if(!t) return 'no-target';
    t.scrollIntoView({ block: 'center' });
    var a = center(chip.querySelector('.nm'));
    var b = center(t);
    pe('pointerdown', a.x, a.y, chip.querySelector('.nm'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    return 'ok';
  }

  /* ---------------- 0. 初始结构与默认值 ---------------- */
  fixture();
  ck('页面上有药盒容器 #box', !!q('#box'), '');
  // ★ 类名撞车体检（2026-10-22 真踩过）：拖拽浮层最初叫 .ghost，和 `.btn.ghost` 撞名，
  //   结果顶栏 5 个按钮一起变成 position:fixed 并跑到 translate(-999px,-999px)，
  //   **页面上直接看不见、控制台一声不响** —— 只有截图能发现。这条断言把那个坑焊死。
  (function(){
    var btns = qa('.top-actions .btn');
    var bad = btns.filter(function(b){
      var cs = getComputedStyle(b), r = b.getBoundingClientRect();
      return cs.position === 'fixed' || r.width <= 0 || r.left < 0;
    });
    ck('顶栏 6 个按钮都在原位（没被拖拽浮层的样式撞飞）',
       btns.length === 6 && bad.length === 0,
       btns.length + ' 个，异常 ' + bad.length + ' 个');
  })();
  ck('默认 3 格', slots().length === 3, slots().length);
  ck('默认格名是 早 / 中 / 晚', slotNames().join('/') === '早/中/晚', slotNames().join('/'));
  ck('空格里写着「拖药进来」', slots().every(function(s){
    return /拖药进来/.test(s.textContent); }), '');
  ck('盒子下面有一句「还空着」的引导', /还空着/.test(boxText()), '');
  ck('格数控件显示「3 格」', /3 格/.test(q('#box .box-ctl .n').textContent),
     q('#box .box-ctl .n').textContent);

  /* ---------------- 1. 从列表拖进格子 ---------------- */
  ck('拖「早饭前片」到第 1 格：结构返回 ok', dragToListCard('早饭前片', 0) === 'ok', '');
  ck('第 1 格里有它了', nameIn(0, '早饭前片'), slotIds(0).length);
  ck('第 1 格的 DOM 里出现这个药名', /早饭前片/.test(slots()[0].textContent), '');
  ck('格子里的药带着剂量（1 片）', /1\s*片/.test(slots()[0].textContent),
     slots()[0].textContent.replace(/\s+/g, ' ').trim());
  ck('放进去之后空格提示消失', !/拖药进来/.test(slots()[0].textContent), '');
  ck('盒子底部的「还空着」引导跟着消失', !/还空着/.test(boxText()), '');

  /* ---------------- 2. 同一个药可以进多格（早晚各一次那种）---------------- */
  dragToListCard('一天两次片', 0);
  dragToListCard('一天两次片', 2);
  ck('「一天两次片」同时在早、晚两格（从列表拖 = 复制一份）',
     nameIn(0, '一天两次片') && nameIn(2, '一天两次片'),
     slotIds(0).length + ' / ' + slotIds(2).length);
  ck('它没有因为拖第二次而从第 1 格消失', nameIn(0, '一天两次片'), slotIds(0).join(','));

  /* ---------------- 3. 格子之间拖 = 搬过去，不是复制 ---------------- */
  dragChip(0, '早饭前片', 1);
  ck('从第 1 格拖到第 2 格：第 2 格有了', nameIn(1, '早饭前片'), slotIds(1).length);
  ck('源格（第 1 格）里已经没有了', !nameIn(0, '早饭前片'), slotIds(0).length);
  ck('搬完之后总数没变多（1 份药还是 1 份）',
     slotIds(0).concat(slotIds(1), slotIds(2)).filter(function(id){
       return id === byName('早饭前片').id; }).length === 1, '');

  /* ---------------- 4. 拖到格子外面 = 什么都不发生 ---------------- */
  var beforeOutside = JSON.stringify(curBox().slots);
  dragToListCard('晚饭后胶囊', 0, { x: 8, y: 8 });
  ck('松手在格子外面 → 一格都没进', JSON.stringify(curBox().slots) === beforeOutside,
     JSON.stringify(curBox().slots) === beforeOutside ? '未变' : '被改了');
  ck('并且给了句提示，不是静悄悄失败', /松手要在格子里/.test(q('#toast').textContent),
     q('#toast').textContent);

  /* ---------------- 5. 只点一下（不移动）不算拖 ---------------- */
  var beforeClick = JSON.stringify(curBox().slots);
  (function(){
    var c = card('晚饭后胶囊');
    var s = slots()[0];
    var a = center(c.querySelector('.med-name'));
    pe('pointerdown', a.x, a.y, c.querySelector('.med-name'));
    pe('pointerup', a.x, a.y);
  })();
  ck('在卡片上原地按一下松手，不会往盒子里加东西',
     JSON.stringify(curBox().slots) === beforeClick, '');

  /* ---------------- 6. 备用药拖不进去 ---------------- */
  var beforePrn = JSON.stringify(curBox().slots);
  dragToListCard('疼了才吃片', 1);
  ck('备用药拖到格子上 → 没进去', JSON.stringify(curBox().slots) === beforePrn, '');
  ck('并且说清了原因（不是默默不理）', /备用药不按天吃/.test(q('#toast').textContent),
     q('#toast').textContent);

  /* ---------------- 7. 点 × 从这一格拿出来 ---------------- */
  dragToListCard('晚饭后胶囊', 2);
  ck('「晚饭后胶囊」先放进第 3 格', nameIn(2, '晚饭后胶囊'), slotIds(2).length);
  (function(){
    var s = slots()[2];
    var m = byName('晚饭后胶囊');
    var x = s.querySelector('.chip[data-med="' + m.id + '"] .x');
    if(x) x.click();
  })();
  ck('点 × 之后这一格没它了', !nameIn(2, '晚饭后胶囊'), slotIds(2).length);
  ck('点 × 不影响它在别的格（本来也不在）', !nameIn(0, '晚饭后胶囊') && !nameIn(1, '晚饭后胶囊'), '');
  ck('药本身还在清单里（只是从盒子拿出来，不是删药）', !!byName('晚饭后胶囊'), '');

  /* ---------------- 8. 格数增减 ---------------- */
  fixture();
  dragToListCard('早饭前片', 0);
  q('#box button[data-box="inc"]').click();
  ck('点 ＋ 变成 4 格', slots().length === 4, slots().length);
  ck('新加的第 4 格默认叫「睡前」', slotNames()[3] === '睡前', slotNames().join('/'));
  ck('加格不会动已经摆好的药', nameIn(0, '早饭前片'), '');
  q('#box button[data-box="dec"]').click();
  ck('点 − 回到 3 格', slots().length === 3, slots().length);
  setSlotCount(99);
  ck('格数上限 7（填 99 也只到 7）', slots().length === 7, slots().length);
  ck('到上限后「＋」按钮是禁用的', q('#box button[data-box="inc"]').disabled === true, '');
  setSlotCount(0);
  ck('格数下限 1（填 0 也留 1 格）', slots().length === 1, slots().length);
  ck('到下限后「−」按钮是禁用的', q('#box button[data-box="dec"]').disabled === true, '');

  // 减格时那一格里的药会被拿掉 —— 但药本身和库存不动
  fixture();
  setSlotCount(4);
  dragToListCard('早饭前片', 3);
  ck('第 4 格里有药', nameIn(3, '早饭前片'), slotIds(3).length);
  var stockBefore = byName('早饭前片').stockBase;
  setSlotCount(3);
  ck('减到 3 格：被砍掉的第 4 格连同里面的药一起没了', slots().length === 3, slots().length);
  ck('减格只是从盒子里拿掉，药还在清单里、库存也没变',
     !!byName('早饭前片') && byName('早饭前片').stockBase === stockBefore,
     byName('早饭前片') ? byName('早饭前片').stockBase : '(药没了)');

  /* ---------------- 9. 格名能改 ---------------- */
  fixture();
  R.promptValue = '早饭前';
  renameSlot(0);
  ck('改格名：第 1 格叫「早饭前」', slotNames()[0] === '早饭前', slotNames()[0]);
  ck('格名写进了数据（不是只在 DOM 上）', curBox().slots[0].name === '早饭前',
     curBox().slots[0].name);
  R.promptValue = '';
  renameSlot(0);
  ck('格名改成空 → 保持原来的名字，不留无名格', curBox().slots[0].name === '早饭前',
     curBox().slots[0].name);
  R.promptValue = null;
  renameSlot(0);   // prompt 返回默认值（原来的名字）→ 不变
  ck('按取消（返回 null）不改名', curBox().slots[0].name === '早饭前', curBox().slots[0].name);
  R.promptValue = '这一格的名字特别特别特别长哦';
  renameSlot(0);
  ck('格名最长 12 个字（不然会把格子撑坏）', curBox().slots[0].name.length === 12,
     curBox().slots[0].name.length);

  /* ---------------- 10. 删药：盒子里的也一起清掉 ---------------- */
  fixture();
  dragToListCard('早饭前片', 0);
  dragToListCard('早饭前片', 1);
  var deadId = byName('早饭前片').id;
  delMed(deadId);
  ck('删掉这味药之后，盒子里一处都不剩',
     curBox().slots.every(function(s){ return s.ids.indexOf(deadId) < 0; }),
     JSON.stringify(curBox().slots.map(function(s){ return s.ids; })));
  ck('格子不会因为查不到药而显示空白药名',
     !/早饭前片/.test(q('#box').textContent), '');
  ck('删完之后格子还在，只是空了', slots().length === 3, slots().length);

  /* ---------------- 11. 换人：各人的盒子互不串 ---------------- */
  fixture();
  dragToListCard('早饭前片', 0);
  var keepId = currentId;
  var yi = { id: uid(), name:'乙', note:'', box: { slots: defaultSlots(2) }, meds:[
    { id: uid(), name:'乙的片', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:10, stockDate:todayStr(), rxMax:30, kind:'daily' }
  ]};
  people.push(yi); save(); switchPerson(yi.id);
  ck('切到「乙」→ 盒子是乙自己的 2 格', slots().length === 2, slots().length);
  ck('乙的盒子是空的（没有把上一个人的药带过来）',
     curBox().slots.every(function(s){ return s.ids.length === 0; }), '');
  dragToListCard('乙的片', 1);
  ck('乙往自己盒子里放药，放得进去', nameIn(1, '乙的片'), curBox().slots[1].ids.length);
  switchPerson(keepId);
  ck('切回来 → 原来的 3 格和原来摆的药都还在',
     slots().length === 3 && nameIn(0, '早饭前片'), slots().length + ' / ' + slotIds(0).length);

  /* ---------------- 12. 存档往返：box 不许在路上丢 / 被洗坏 ---------------- */
  fixture();
  dragToListCard('早饭前片', 0);
  dragToListCard('晚饭后胶囊', 2);
  curBox().slots[0].name = '早饭前';
  save();
  var snap = JSON.parse(JSON.stringify({ v: 2, currentId: currentId, people: people }));
  var curInSnap = snap.people.filter(function(p){ return p.id === snap.currentId; })[0];
  ck('导出快照里带着 box（3 格）', !!curInSnap.box && curInSnap.box.slots.length === 3,
     curInSnap.box ? curInSnap.box.slots.length : '(没有 box)');
  var back = migrate(snap);
  var curInBack = back.people.filter(function(p){ return p.id === snap.currentId; })[0];
  ck('导入后格数还是 3', curInBack.box.slots.length === 3, curInBack.box.slots.length);
  ck('导入后格名还在（「早饭前」没被洗回「早」）', curInBack.box.slots[0].name === '早饭前',
     curInBack.box.slots[0].name);
  ck('导入后每一格里的药 id 一个不少',
     curInBack.box.slots[0].ids.length === 1 && curInBack.box.slots[2].ids.length === 1,
     curInBack.box.slots.map(function(s){ return s.ids.length; }).join('/'));

  // 老存档（压根没有 box 字段）→ 补默认 3 格，不能报错
  var old = migrate({ v: 2, currentId: 'p1', people: [
    { id: 'p1', name: '我', note: '', meds: [] }
  ]});
  ck('老存档没有 box → 补默认 3 格', old.people[0].box.slots.length === 3,
     old.people[0].box.slots.length);
  ck('老存档补出来的格名也是 早/中/晚',
     old.people[0].box.slots.map(function(s){ return s.name; }).join('/') === '早/中/晚',
     old.people[0].box.slots.map(function(s){ return s.name; }).join('/'));
  // 脏数据：格数超上限、格名空的、ids 里混了非字符串
  var dirty = migrate({ v: 2, currentId: 'p1', people: [
    { id: 'p1', name: '我', meds: [], box: { slots: [
      { name: '  ', ids: ['a', null, 3, 'b'] },
      { ids: ['c'] }, { name: 'x', ids: [] }, { name: 'y', ids: [] },
      { name: 'z', ids: [] }, { name: 'w', ids: [] }, { name: 'v', ids: [] }, { name: 'u', ids: [] }
    ] } }
  ]});
  ck('脏数据：格数被截到 7', dirty.people[0].box.slots.length === 7,
     dirty.people[0].box.slots.length);
  ck('脏数据：空格名补成默认名', dirty.people[0].box.slots[0].name === '早',
     dirty.people[0].box.slots[0].name);
  ck('脏数据：没名字的格也补上', dirty.people[0].box.slots[1].name === '中',
     dirty.people[0].box.slots[1].name);
  ck('脏数据：ids 只留字符串（非字符串被剔掉）',
     dirty.people[0].box.slots[0].ids.join(',') === 'a,b',
     dirty.people[0].box.slots[0].ids.join(','));

  /* ---------------- 13. 盒子里不留野指针 ---------------- */
  fixture();
  var p0 = curPerson();
  p0.box = { slots: [ { name: '早', ids: ['不存在的id'] }, { name: '中', ids: [] } ] };
  render();
  ck('格子里放着查不到的 id → 渲染成空格，不显示空壳',
     /拖药进来/.test(slots()[0].textContent), slots()[0].textContent.trim());
  ck('渲染后没把野 id 清掉（只跳过，不动数据 —— 万一是导入瞬间的时序问题还能救回来）',
     curBox().slots[0].ids.length === 1, curBox().slots[0].ids.length);

  function finish(){
    var pre = document.createElement('pre');
    pre.id = '__T';
    pre.textContent = JSON.stringify(R);
    document.body.appendChild(pre);
  }
  finish();
})();
"""


def main():
    src = open(SRC, encoding="utf-8").read()
    anchor = "})();\n</script>"
    if anchor not in src:
        print("找不到主 IIFE 结尾锚点")
        return 2
    i = src.rindex(anchor)
    tmpdir = tempfile.mkdtemp(prefix="dk_box_")
    page = os.path.join(tmpdir, "t.html")
    open(page, "w", encoding="utf-8").write(src[:i] + PROBE + src[i:])

    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--window-size=1280,1400",
           "--virtual-time-budget=8000",
           "--user-data-dir=" + os.path.join(tmpdir, "prof"), "--dump-dom", url]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    dom = r.stdout.decode("utf-8", "replace")
    dom_ns = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
    mm = re.search(r'<pre id="__T">(.*?)</pre>', dom_ns, flags=re.S)
    if not mm:
        print("!! 没拿到测试输出，DOM 长度", len(dom))
        print(dom_ns[:1500])
        return 3
    data = json.loads(html.unescape(mm.group(1)))

    fails = [c for c in data["cases"] if not c["pass"]]
    for c in data["cases"]:
        print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c["got"]))
    print("\n" + "-" * 60)
    print("通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
