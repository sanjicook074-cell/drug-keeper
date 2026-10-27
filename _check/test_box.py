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
/* ★ async：2026-10-23 起页面里的确认/提示/输入框全换成自定义 <dialog>，
   askConfirm/askText 都返回 Promise，调用处必须 await —— 同步 IIFE 拿不到结果。 */
(async function(){
  var R = { alerts: [], cases: [], prompts: [], natives: [] };
  window.__T = R;
  // ★ 2026-10-23：页面里的确认/提示/输入框全部换成页面内 <dialog>（用户要求"不要用页面默认方式"）。
  //   这三个桩不再用来"模拟用户回答"，而是**记录违规** —— 页面里只要还有一处走原生弹窗，
  //   R.natives 就非空，最后那条断言会红。返回 true 只是让残留代码不至于卡住流程。
  window.alert = function(m){ R.natives.push('alert: ' + String(m)); };
  window.confirm = function(m){ R.natives.push('confirm: ' + String(m)); return true; };
  window.prompt = function(m){ R.natives.push('prompt: ' + String(m)); return ''; };
  try {
  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }

  /* ---- 通用弹窗的"出手"工具 ----
     askConfirm / askText 返回 Promise，而且弹窗在调用那一刻就同步打开了
     （Promise executor 是同步跑的），所以可以"先点按钮、再等结果"：
         await ans(setSlotCount(3), '减到')
         await ansText(renameSlot(0), '早饭前')
     btn 传按钮文字的一部分；'esc' 模拟按 Esc；不传 = 点主按钮。
     ★ 不桩掉 askConfirm 是有意的：这些断言要顺带证明"弹窗真的弹出来了、
       按钮真的能点、点取消真的什么都没发生"，桩掉就只剩函数调用了。 */
  function tapAsk(btn){
    var d = q('#dlgAsk');
    if(!d || !d.open) return false;
    if(btn === 'esc'){
      d.dispatchEvent(new Event('cancel', { cancelable: true }));
      return true;
    }
    var btns = Array.prototype.slice.call(d.querySelectorAll('#askFoot .btn'));
    var pick = null, i;
    if(btn){
      for(i = 0; i < btns.length; i++){
        if(btns[i].textContent.indexOf(btn) >= 0){ pick = btns[i]; break; }
      }
    } else {
      pick = d.querySelector('#askFoot .btn.primary') || d.querySelector('#askFoot .btn.danger');
      if(!pick && btns.length) pick = btns[btns.length - 1];
    }
    if(!pick) return false;
    pick.click();
    return true;
  }
  async function ans(p, btn){
    await null;                                   // 让 async 函数先跑到 await 处（弹窗已在此时打开）
    var d = q('#dlgAsk');
    if(d && d.open && !tapAsk(btn)){
      throw new Error('弹窗开着，但按钮「' + (btn || '(主按钮)') + '」没找到');
    }
    return await p;
  }
  // 输入框版：先填字，再点按钮
  async function ansText(p, val, btn){
    await null;
    var d = q('#dlgAsk');
    if(d && d.open){
      var inp = q('#askInput');
      if(inp) inp.value = val;
      if(!tapAsk(btn)) throw new Error('输入弹窗里找不到按钮「' + (btn || '(主按钮)') + '」');
    }
    return await p;
  }
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
    // ★ 2026-10-23 起多了第 8 个按钮 #btnMore（手机版把六个次要动作收进「⋯」），
    //   它在桌面上必须是 display:none —— 所以这里只数**可见**的那几个，
    //   再单独验一句「手机上那片零件在桌面上确实藏着」。
    var all = qa('.top-actions .btn');
    var btns = all.filter(function(b){ return getComputedStyle(b).display !== 'none'; });
    var bad = btns.filter(function(b){
      var cs = getComputedStyle(b), r = b.getBoundingClientRect();
      return cs.position === 'fixed' || r.width <= 0 || r.left < 0;
    });
    ck('顶栏 7 个按钮都在原位（没被拖拽浮层的样式撞飞）',
       btns.length === 7 && bad.length === 0,
       btns.length + ' 个可见，异常 ' + bad.length + ' 个');
    var more = q('#btnMore');
    var moreDisp = more ? getComputedStyle(more).display : '(不存在)';
    ck('桌面版没有多余的「⋯」：手机专用零件在桌面上是隐藏的',
       moreDisp === 'none', moreDisp);
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
  await setSlotCount(99);
  ck('格数上限 7（填 99 也只到 7）', slots().length === 7, slots().length);
  ck('到上限后「＋」按钮是禁用的', q('#box button[data-box="inc"]').disabled === true, '');
  await setSlotCount(0);
  ck('格数下限 1（填 0 也留 1 格）', slots().length === 1, slots().length);
  ck('到下限后「−」按钮是禁用的', q('#box button[data-box="dec"]').disabled === true, '');

  // 减格时那一格里的药会被拿掉 —— 但药本身和库存不动
  fixture();
  await setSlotCount(4);
  dragToListCard('早饭前片', 3);
  ck('第 4 格里有药', nameIn(3, '早饭前片'), slotIds(3).length);
  var stockBefore = byName('早饭前片').stockBase;
  // 砍掉的格里还放着药 → 必须弹窗确认（原来走原生 confirm，现在是页面内 <dialog>）
  var pCut = setSlotCount(3);
  ck('减到 3 格之前先弹了确认框（页面内 dialog，不是原生 confirm）',
     !!(q('#dlgAsk') && q('#dlgAsk').open), '');
  ck('确认框把要砍掉的格名说出来了', /睡前/.test(q('#askSub').textContent), q('#askSub').textContent);
  await ans(pCut, '减到');
  ck('减到 3 格：被砍掉的第 4 格连同里面的药一起没了', slots().length === 3, slots().length);
  ck('减格只是从盒子里拿掉，药还在清单里、库存也没变',
     !!byName('早饭前片') && byName('早饭前片').stockBase === stockBefore,
     byName('早饭前片') ? byName('早饭前片').stockBase : '(药没了)');

  /* ---------------- 8b. 单格删除：指定删哪一格（2026-10-27 用户要求）----------------
     用户原话「药盒里 每一格再加一个删除功能 可以指定删除那一格」。
     上面那排 −／＋ 只能从末尾砍，删中间某一格得先减格再补回来 —— 所以每格自己带一个叉。 */
  fixture();
  await setSlotCount(4);
  dragToListCard('早饭前片', 1);            // 摆在第 2 格（默认叫「中」）
  dragToListCard('晚饭后胶囊', 3);          // 摆在第 4 格（默认叫「睡前」）
  ck('每一格都有自己的删除叉', qa('#box .slot-x').length === 4, qa('#box .slot-x').length);
  var keepStock = byName('早饭前片').stockBase;

  // 删带药的格 → 先问（问清是哪一格、里面有什么、药会不会丢）
  var pDel = delSlot(1);
  ck('删带药的格之前先弹确认框（页面内 dialog，不是原生 confirm）',
     !!(q('#dlgAsk') && q('#dlgAsk').open), '');
  ck('确认框标题点名是哪一格', /删掉「中」/.test(q('#askTitle').textContent),
     q('#askTitle').textContent);
  ck('确认框把这一格里的药名列出来了', /早饭前片/.test(q('#askSub').textContent),
     q('#askSub').textContent);
  ck('确认框说清药不会丢（只是不再摆在这一格）',
     /库存都不动/.test((q('#askBody .ask-warn') || {textContent: ''}).textContent),
     (q('#askBody .ask-warn') || {textContent: '(没有 warn 段)'}).textContent);
  tapAsk('取消');
  await pDel;
  ck('点取消：一格都没少', slots().length === 4, slots().length);
  ck('点取消：那味药还在格里', nameIn(1, '早饭前片'), '');

  await ans(delSlot(1), '删掉');
  ck('删掉第 2 格之后只剩 3 格', slots().length === 3, slots().length);
  ck('★ 删的是指定的那一格，不是末尾 —— 原来第 4 格那味药还在盒里',
     nameIn(2, '晚饭后胶囊'), '第 3 格：' + slotIds(2).length + ' 味');
  ck('删格只是把药从盒里拿出来：药还在、库存没变',
     !!byName('早饭前片') && byName('早饭前片').stockBase === keepStock,
     byName('早饭前片') ? byName('早饭前片').stockBase : '(药没了)');
  ck('★ 被拿出来的药自动回到「未入清单」', upNames().indexOf('早饭前片') >= 0,
     upNames().join('、'));

  // 空格子不问，直接删 —— 问一次纯属多一步，反正随时能「＋」加回来
  fixture();
  await setSlotCount(3);
  var pEmpty = delSlot(2);
  ck('删空格子不弹确认框', !(q('#dlgAsk') && q('#dlgAsk').open), q('#dlgAsk').open);
  await pEmpty;
  ck('空格子直接删掉', slots().length === 2, slots().length);

  // 只剩一格时不许删：叉是隐藏的（disabled），delSlot 里还有一层兜底
  fixture();
  await setSlotCount(1);
  ck('只剩一格时那个叉是隐藏的（disabled）', q('#box .slot-x').disabled === true, '');
  var pMin = delSlot(0);
  ck('兜底：只剩一格时硬删会弹提示，不是默默不动',
     !!(q('#dlgAsk') && q('#dlgAsk').open), '');
  ck('提示说清了原因', /至少要留一格/.test(q('#askSub').textContent), q('#askSub').textContent);
  tapAsk();
  await pMin;
  ck('提示之后还是一格', slots().length === 1, slots().length);

  // ★ 两个叉长得一模一样，最容易串的地方：格名旁边那个必须删格，胶囊里那个必须拿药
  fixture();
  dragToListCard('早饭前片', 0);
  q('#box .slot[data-slot="0"] .slot-x').click();
  ck('点格名旁边的叉 = 删格（弹的是删格框，标题带格名）',
     /删掉「早」/.test(q('#askTitle').textContent), q('#askTitle').textContent);
  await ans(null, '取消');
  ck('取消之后药还在格里（没被误当成「从这格拿出来」）', nameIn(0, '早饭前片'), '');
  q('#box .chip .x').click();
  ck('点胶囊上的叉 = 把药拿出来，格子还在',
     slots().length === 3 && !nameIn(0, '早饭前片'),
     slots().length + ' 格 / ' + slotIds(0).length + ' 味');

  /* ---------------- 9. 格名能改 ---------------- */
  fixture();
  // 改格名原来是原生 prompt，现在是页面内的输入弹窗 —— 顺带查它真的弹了、预填了原名字
  var pRename = renameSlot(0);
  ck('改格名弹的是页面内输入框（不是原生 prompt）',
     !!(q('#dlgAsk') && q('#dlgAsk').open && q('#askInput')), '');
  ck('输入框预填了这一格现在的名字（回车不改 = 原名）',
     q('#askInput').value === curBox().slots[0].name,
     q('#askInput').value + ' / ' + curBox().slots[0].name);
  tapAsk('取消');
  await pRename;
  await ansText(renameSlot(0), '早饭前');
  ck('改格名：第 1 格叫「早饭前」', slotNames()[0] === '早饭前', slotNames()[0]);
  ck('格名写进了数据（不是只在 DOM 上）', curBox().slots[0].name === '早饭前',
     curBox().slots[0].name);
  await ansText(renameSlot(0), '');
  ck('格名改成空 → 保持原来的名字，不留无名格', curBox().slots[0].name === '早饭前',
     curBox().slots[0].name);
  await ansText(renameSlot(0), '不该生效', '取消');
  ck('按取消不改名', curBox().slots[0].name === '早饭前', curBox().slots[0].name);
  await ansText(renameSlot(0), '这一格的名字特别特别特别长哦');
  ck('格名最长 12 个字（不然会把格子撑坏）', curBox().slots[0].name.length === 12,
     curBox().slots[0].name.length);

  /* ---------------- 10. 删药：盒子里的也一起清掉 ---------------- */
  fixture();
  dragToListCard('早饭前片', 0);
  dragToListCard('早饭前片', 1);
  var deadId = byName('早饭前片').id;
  // 删药先弹确认框：查它弹了、写清了连带影响、点「取消」绝不删
  var pDel = delMed(deadId);
  (function(){
    var d = q('#dlgAsk');
    R.dlg = { open: !!(d && d.open),
              title: d ? q('#askTitle').textContent : '',
              sub: d ? q('#askSub').textContent : '',
              hasDanger: !!(d && d.querySelector('#askFoot .btn.danger')) };
  })();
  await ans(pDel, '取消');
  ck('删药先弹页面内确认框（不是原生 confirm）', R.dlg.open === true, R.dlg.open);
  ck('确认框标题带药名', /早饭前片/.test(R.dlg.title), R.dlg.title);
  ck('确认框说明了「药盒里也摆着它」这个连带影响', /药盒/.test(R.dlg.sub), R.dlg.sub);
  ck('删除按钮是危险色（不是普通主按钮）', R.dlg.hasDanger === true, R.dlg.hasDanger);
  ck('★ 点「取消」之后药还在（没删）', !!byName('早饭前片'), byName('早饭前片') ? '在' : '没了');
  ck('点「取消」之后盒子里那两份也还在',
     slotIds(0).indexOf(deadId) >= 0 && slotIds(1).indexOf(deadId) >= 0, '');

  await ans(delMed(deadId), '删除');
  ck('点「删除」之后清单里没它了', !byName('早饭前片'), byName('早饭前片') ? '还在' : '没了');
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

  /* ---------------- 14. 未入清单（2026-10-23 用户要求）----------------
     原话：「药盒的下面应该有一个 未入清单 里面是全部每天吃的药
            然后 未入清单的药可以拖进药盒」
     钉四件事：① 位置在药盒下面；② 内容是「每天吃的药 − 已入盒的」；
               ③ 拖得进格子；④ 反过来拖回清单 = 从盒子里拿出来。 */
  function upZone(){ return q('#box .unplaced'); }
  function upItems(){ return qa('#box .up-item'); }
  function upNames(){
    return upItems().map(function(e){ return e.querySelector('.nm').textContent.trim(); });
  }
  function upItem(nm){
    var its = upItems();
    for(var k = 0; k < its.length; k++){
      if(its[k].querySelector('.nm').textContent.trim() === nm) return its[k];
    }
    return null;
  }
  // 从清单里某味药拖到第 i 格
  function dragFromUp(nm, i){
    var it = upItem(nm);
    if(!it) return 'no-item';
    var s = slots()[i];
    if(!s) return 'no-slot';
    s.scrollIntoView({ block: 'center' });
    var h = it.querySelector('.nm');
    var a = center(h);
    var b = center(s);
    pe('pointerdown', a.x, a.y, h);
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    return 'ok';
  }
  // 从第 i 格里的某味药拖回未入清单
  function dragChipToUp(i, nm){
    var s = slots()[i];
    if(!s) return 'no-slot';
    var m = byName(nm);
    var chip = s.querySelector('.chip[data-med="' + (m ? m.id : '') + '"]');
    if(!chip) return 'no-chip';
    var z = upZone();
    if(!z) return 'no-zone';
    z.scrollIntoView({ block: 'center' });
    var h = chip.querySelector('.nm');
    var a = center(h);
    var b = center(z.querySelector('.up-h'));
    pe('pointerdown', a.x, a.y, h);
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    return 'ok';
  }

  fixture();
  var slotsEl = q('#box .slots');
  ck('药盒下面确实有一块「未入清单」', !!upZone() && /未入清单/.test(upZone().textContent), '');
  ck('它在 DOM 里排在格子后面',
     Array.prototype.indexOf.call(q('#box').children, upZone()) >
     Array.prototype.indexOf.call(q('#box').children, slotsEl),
     Array.prototype.indexOf.call(q('#box').children, upZone()) + ' > ' +
     Array.prototype.indexOf.call(q('#box').children, slotsEl));
  ck('视觉上也在格子下面（不是靠 CSS 挪上去的）',
     upZone().getBoundingClientRect().top >= slotsEl.getBoundingClientRect().bottom - 2,
     Math.round(upZone().getBoundingClientRect().top) + ' vs ' +
     Math.round(slotsEl.getBoundingClientRect().bottom));

  ck('初始 3 味每天吃的药全在清单里（盒子还空着）',
     upNames().length === 3, upNames().join('、'));
  ck('清单里的顺序跟药品列表一致',
     upNames().join('、') === '早饭前片、晚饭后胶囊、一天两次片', upNames().join('、'));
  ck('备用药不在清单里（它本来就不进盒）',
     upNames().indexOf('疼了才吃片') < 0, upNames().join('、'));
  ck('清单标题旁边有数量（3）',
     (upZone().querySelector('.up-n') || {}).textContent === '3',
     (upZone().querySelector('.up-n') || {}).textContent);
  ck('清单里的药带着剂量（1 片 / 2 粒）',
     /1\s*片/.test(upItem('早饭前片').textContent) && /2\s*粒/.test(upItem('晚饭后胶囊').textContent),
     upItem('早饭前片').textContent.replace(/\s+/g, ' ').trim());
  ck('清单项上没有 × 按钮（它本来就不在盒里，没什么可拿出来的）',
     !upItem('早饭前片').querySelector('.x'), '');

  // 拖进格子
  ck('从清单拖「早饭前片」到第 1 格：结构返回 ok', dragFromUp('早饭前片', 0) === 'ok', '');
  ck('第 1 格里有它了', nameIn(0, '早饭前片'), slotIds(0).length);
  ck('它从清单里消失了（进了盒就不该再躺在那儿）',
     upNames().indexOf('早饭前片') < 0, upNames().join('、'));
  ck('清单数量跟着变成 2', upNames().length === 2, upNames().length);
  ck('别的药没被顺带挪动', upNames().join('、') === '晚饭后胶囊、一天两次片',
     upNames().join('、'));
  ck('已入盒的药不会再出现在清单里（拖第二次找不到它）',
     dragFromUp('早饭前片', 1) === 'no-item', 'no-item');

  // 同一味药可以进多格，清单里不会冒重复项
  // 同一味药进两格（早晚各一次那种）：清单里只能拖出第一份，
  // 第二份要从药品列表拖 —— 入盒之后它就不在清单里了，这是有意的。
  dragFromUp('一天两次片', 0);
  dragToListCard('一天两次片', 2);
  ck('同一味药进两格：第 1 格有', nameIn(0, '一天两次片'), slotIds(0).length);
  ck('同一味药进两格：第 3 格也有', nameIn(2, '一天两次片'),
     slotIds(0).length + '|' + slotIds(2).length);
  ck('同一味药进两格，清单里也不会冒出两份',
     JSON.stringify(upNames()) === JSON.stringify(['晚饭后胶囊']), upNames().join('、'));

  // 拖回清单 = 从盒子里拿出来
  ck('从第 1 格把「一天两次片」拖回清单：结构返回 ok',
     dragChipToUp(0, '一天两次片') === 'ok', '');
  ck('第 1 格没它了', !nameIn(0, '一天两次片'), slotIds(0).length);
  ck('第 3 格那份还在（只拿了源格那一份）', nameIn(2, '一天两次片'), slotIds(2).length);
  ck('它没回到清单（另一格还摆着，这味药仍在盒里）',
     upNames().indexOf('一天两次片') < 0, upNames().join('、'));
  ck('药本身和库存都没动（只是从盒子拿出来）',
     !!byName('一天两次片') && byName('一天两次片').stockBase === 60,
     byName('一天两次片') ? byName('一天两次片').stockBase : '(药没了)');

  dragChipToUp(2, '一天两次片');
  ck('最后一份也拖回清单后，清单里有它了', upNames().indexOf('一天两次片') >= 0,
     upNames().join('、'));
  ck('清单里不会因为它进过两格就出现两次',
     upNames().filter(function(n){ return n === '一天两次片'; }).length === 1,
     upNames().join('、'));

  // 点 × 也是拿出盒子，同样回到清单
  dragFromUp('晚饭后胶囊', 1);
  (function(){
    var m = byName('晚饭后胶囊');
    var x = slots()[1].querySelector('.chip[data-med="' + m.id + '"] .x');
    if(x) x.click();
  })();
  ck('点 × 之后它自动回到清单', upNames().indexOf('晚饭后胶囊') >= 0, upNames().join('、'));

  // 全部入盒
  fixture();
  dragFromUp('早饭前片', 0);
  dragFromUp('晚饭后胶囊', 0);
  dragFromUp('一天两次片', 0);
  ck('全部摆进盒子后，清单提示「都摆进盒子里了」',
     /都摆进盒子里了/.test(upZone().textContent), upZone().textContent.replace(/\s+/g, ' ').trim());
  ck('空清单也保留落点（想从盒里拿出来还拖得进去）',
     !!upZone().getAttribute('data-dropzone'), '');
  ck('空清单时从格子拖回来仍然生效',
     dragChipToUp(0, '早饭前片') === 'ok' && nameIn(0, '早饭前片') === false &&
     upNames().indexOf('早饭前片') >= 0, upNames().join('、'));

  // 只有备用药的人
  (function(){
    var p = curPerson();
    p.box = { slots: defaultSlots(3) };
    setMeds([ { id: uid(), name:'疼了才吃片', spec:'0.3g', perDose:1, unit:'片',
                cycleDays:1, timesPerCycle:0, stockBase:8, stockDate:todayStr(),
                rxMax:30, kind:'prn' } ]);
    save(); render();
    ck('一个人只有备用药 → 清单写「还没有每天吃的药」',
       /还没有每天吃的药/.test(upZone().textContent),
       upZone().textContent.replace(/\s+/g, ' ').trim());
    ck('并且一条都不列', upItems().length === 0, upItems().length);
  })();

  // 用光的药还在清单里（不能因为余量 0 就把待摆的药藏起来）
  fixture();
  byName('早饭前片').stockBase = 0;
  byName('早饭前片').stockDate = agoD(30);
  render();
  ck('已经吃完的药仍然留在清单里（它还是要摆进盒子的）',
     upNames().indexOf('早饭前片') >= 0, upNames().join('、'));
  ck('用光的药在清单里有淡化标记（data-lv=out）',
     upItem('早饭前片').getAttribute('data-lv') === 'out',
     upItem('早饭前片').getAttribute('data-lv'));

  // 换人：清单跟着当前人走
  fixture();
  dragFromUp('早饭前片', 0);
  var upKeep = currentId;
  var yi2 = { id: uid(), name:'乙', note:'', box: { slots: defaultSlots(2) }, meds:[
    { id: uid(), name:'乙的片', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:10, stockDate:todayStr(), rxMax:30, kind:'daily' }
  ]};
  people.push(yi2); save(); switchPerson(yi2.id);
  ck('换到乙：清单只列乙自己的药', upNames().join('、') === '乙的片', upNames().join('、'));
  ck('换到乙：甲已入盒的药没有漏过来', upNames().indexOf('早饭前片') < 0, upNames().join('、'));
  switchPerson(upKeep);
  ck('切回来：清单里是甲剩下没入盒的两味',
     upNames().join('、') === '晚饭后胶囊、一天两次片', upNames().join('、'));

  // 从清单拖出去但没落在格子上 → 什么都不发生
  var beforeUpMiss = JSON.stringify(curBox().slots);
  (function(){
    var it = upItem('晚饭后胶囊');
    var a = center(it.querySelector('.nm'));
    pe('pointerdown', a.x, a.y, it.querySelector('.nm'));
    pe('pointermove', a.x + 40, a.y + 40);
    pe('pointerup', 8, 8);
  })();
  ck('从清单拖出去但没落在格子里 → 盒子不变',
     JSON.stringify(curBox().slots) === beforeUpMiss, '');
  ck('并且给出了提示', /松手要在格子里/.test(q('#toast').textContent), q('#toast').textContent);

  /* ---------------- 15. 药名显示不全时折行（2026-10-23 用户要求）----------------
     原话：「药盒和未入库的药名 如果显示不全 可以多行显示」
     ★ 这里量的是**真实布局高度**，不是查 CSS 里写没写 line-clamp ——
       line-clamp 漏写 -webkit-box 显示模式就完全不生效，只查声明是查不出来的。 */
  fixture();
  setSlotCount(7);                                   // 7 格最窄，最容易把名字挤断
  (function(){
    var m = byName('早饭前片');
    m.name = '复方甲氧那明胶囊剂';                    // 9 个字，窄格里一行放不下
    curBox().slots[0].ids = [m.id];
    save(); render();
  })();
  (function(){
    var nm = slots()[0].querySelector('.chip .nm');
    var cs = getComputedStyle(nm);
    var lh = parseFloat(cs.lineHeight);
    var h = nm.getBoundingClientRect().height;
    ck('长药名在窄格里真的折成了多行（不是一行省略号）',
       h > lh * 1.5, h.toFixed(1) + 'px / 行高 ' + lh);
    ck('最多折三行，再长才省略（不会把格子撑成半屏）',
       h <= lh * 3.1, h.toFixed(1) + 'px / 行高 ' + lh);
    ck('药名不再被 nowrap 锁死在一行', cs.whiteSpace !== 'nowrap', cs.whiteSpace);
    // ★ 最硬的一条：clamp 范围内没有内容被裁掉。
    //   有省略号时 scrollHeight 会比 clientHeight 大，这一条能直接抓到"看起来折了但尾巴还是被切"。
    ck('9 字药名在 7 格最窄处完整显示，没有被省略号切掉',
       nm.scrollHeight <= nm.clientHeight + 1,
       'scroll=' + nm.scrollHeight + ' client=' + nm.clientHeight);
    var chip = slots()[0].querySelector('.chip');
    var slot = slots()[0];
    ck('折行之后药片条没溢出格子（右侧不被切）',
       chip.getBoundingClientRect().right <= slot.getBoundingClientRect().right + 0.5,
       Math.round(chip.getBoundingClientRect().right) + ' vs ' +
       Math.round(slot.getBoundingClientRect().right));
    // 窄格时剂量和 × 被甩到下一行，把整行宽度让给药名（flex-wrap 的作用）
    var dose = chip.querySelector('.dose');
    ck('7 格窄条里剂量换到了另一行（药名独占整行宽度）',
       dose.getBoundingClientRect().top >= nm.getBoundingClientRect().bottom - 1,
       'dose.top=' + Math.round(dose.getBoundingClientRect().top) +
       ' nm.bottom=' + Math.round(nm.getBoundingClientRect().bottom));
  })();
  (function(){
    // ★ 别拿刚放进格子的那味来查 —— 它已经不在清单里了（这正是未入清单的规则）
    var first = upItems()[0];
    if(!first){ ck('未入清单里有可查的项', false, '清单是空的'); return; }
    var nm = first.querySelector('.nm');
    var cs = getComputedStyle(nm);
    ck('未入清单里的药名同样允许折行（配置与药盒一致）',
       cs.webkitLineClamp === '3' && cs.whiteSpace !== 'nowrap',
       'clamp=' + cs.webkitLineClamp + ' ws=' + cs.whiteSpace);
  })();

  /* ---------------- 16. 摆药台：一天三顿的药，从台面反复往格子里拖 ----------------
     用户原话：「因为有些药是一天三顿的 未入清单里的药如果只能拖拽一次 会导致使用不便
              我觉得 应该在未入清单的下面再加一个格子 药品拖入这个格子可以多次复制
              然后再点叉把这个药清掉」
     ★ 核心断言只有一条：**同一味药从台面连拖三次，三个格子里都有，台面上还留着**。
       这一条错了，这个功能就等于没做。其余都是围着它的边界。 */
  function stgEl(){ return q('#box .stage'); }
  function stgItems(){ return qa('#box .stg-item'); }
  function stgNames(){
    return stgItems().map(function(e){ return e.querySelector('.nm').textContent.trim(); });
  }
  function stgItemOf(nm){
    var m = byName(nm);
    if(!m) return null;
    var its = stgItems();
    for(var i = 0; i < its.length; i++){
      if(its[i].getAttribute('data-med') === m.id) return its[i];
    }
    return null;
  }
  function stageIds(){ return curStage().slice(); }
  // 从药品列表的卡片拖到台面
  function dragCardToStage(nm){
    var c = card(nm);
    if(!c) return 'no-card';
    var st = stgEl();
    if(!st) return 'no-stage';
    st.scrollIntoView({ block: 'center' });
    var a = center(c.querySelector('.med-name'));
    var b = center(st);
    pe('pointerdown', a.x, a.y, c.querySelector('.med-name'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    return 'ok';
  }
  // 从台面拖到第 i 格
  function dragStageToSlot(nm, i){
    var it = stgItemOf(nm);
    if(!it) return 'no-item';
    var s = slots()[i];
    if(!s) return 'no-slot';
    s.scrollIntoView({ block: 'center' });
    var a = center(it.querySelector('.nm'));
    var b = center(s);
    pe('pointerdown', a.x, a.y, it.querySelector('.nm'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    return 'ok';
  }

  fixture();
  ck('药盒下面有摆药台', !!stgEl(), '');
  ck('摆药台在未入清单下面（DOM 顺序：清单 → 台面）', (function(){
    var all = qa('#box > *');
    var iUp = -1, iStg = -1;
    all.forEach(function(e, k){
      if(e.classList.contains('unplaced')) iUp = k;
      if(e.classList.contains('stage')) iStg = k;
    });
    return iUp >= 0 && iStg > iUp;
  })(), '');
  ck('台面空着时显示占位提示', /台面空着/.test(stgEl().textContent), stgEl().textContent.trim());

  // —— 上台面 ——
  var stockBefore = byName('一天两次片').stockBase;
  ck('把「一天两次片」拖上台面：结构返回 ok', dragCardToStage('一天两次片') === 'ok', '');
  ck('台面上有它了', stgNames().join('、') === '一天两次片', stgNames().join('、'));
  ck('上台面不动库存', byName('一天两次片').stockBase === stockBefore, byName('一天两次片').stockBase);
  ck('上台面也没往格子里放东西', slotIds(0).length + slotIds(1).length + slotIds(2).length === 0, '');
  ck('上台面的药从「未入清单」里退出（三个区域互补，不重复显示）',
     upNames().indexOf('一天两次片') < 0, upNames().join('、'));
  ck('未入清单里另外两味还在', upNames().join('、') === '早饭前片、晚饭后胶囊', upNames().join('、'));

  // —— 核心：同一味药，从台面连拖三次到三个格子 ——
  ck('第 1 次拖到第 1 格：返回 ok', dragStageToSlot('一天两次片', 0) === 'ok', '');
  ck('第 1 格里有它', nameIn(0, '一天两次片'), slotIds(0).length);
  ck('★ 拖完第 1 格，台面上还留着（能再拖）', stgNames().join('、') === '一天两次片',
     stgNames().join('、'));
  ck('第 2 次拖到第 2 格：返回 ok', dragStageToSlot('一天两次片', 1) === 'ok', '');
  ck('第 2 格也有它', nameIn(1, '一天两次片'), slotIds(1).length);
  ck('★ 拖完第 2 格，台面上仍然留着', stgNames().join('、') === '一天两次片', stgNames().join('、'));
  ck('第 3 次拖到第 3 格：返回 ok', dragStageToSlot('一天两次片', 2) === 'ok', '');
  ck('★ 一天三顿的场景成立：三格各一份', nameIn(0, '一天两次片') && nameIn(1, '一天两次片')
     && nameIn(2, '一天两次片'), slotIds(0).length + '|' + slotIds(1).length + '|' + slotIds(2).length);
  ck('三格都放完了，台面上还是那一份（没有变成三份）',
     stgNames().join('、') === '一天两次片', stgNames().length + ' 项');
  ck('台面没有因为拖三次而少一味', stageIds().length === 1, stageIds().length);

  // —— 同一格拖两次不会重复 ——
  dragStageToSlot('一天两次片', 0);
  ck('同一格再拖一次不会塞进第二份', slotIds(0).filter(function(x){
    return x === byName('一天两次片').id; }).length === 1, slotIds(0).length);
  ck('并且提示「已经在这一格里了」', /已经在这一格里了/.test(q('#toast').textContent),
     q('#toast').textContent);

  // —— 点 × 收走 ——
  ck('台面上的药带一个 × 按钮', !!stgItemOf('一天两次片').querySelector('button[data-stgdel]'), '');
  stgItemOf('一天两次片').querySelector('button[data-stgdel]').click();
  ck('点 × 把药从台面收走', stgNames().length === 0 && stageIds().length === 0, stgNames().join('、'));
  ck('收走之后台面回到空态提示', /台面空着/.test(stgEl().textContent), '');
  ck('★ 收走台面不影响已经摆好的三格', nameIn(0, '一天两次片') && nameIn(1, '一天两次片')
     && nameIn(2, '一天两次片'), slotIds(0).length + slotIds(1).length + slotIds(2).length);

  // —— 重复上台面不会出现两份 ——
  dragCardToStage('早饭前片');
  dragCardToStage('早饭前片');
  ck('同一味药重复拖上台面，台面上只有一份', stgNames().length === 1, stgNames().join('、'));
  ck('并提示已经在台面上', /已经在台面上了/.test(q('#toast').textContent), q('#toast').textContent);

  // —— 备用药不许上台面 ——
  dragCardToStage('疼了才吃片');
  ck('备用药拖不上台面', stgNames().join('、') === '早饭前片', stgNames().join('、'));
  ck('并说明理由', /备用药不按天吃/.test(q('#toast').textContent), q('#toast').textContent);

  // —— 从「未入清单」也能拖上台面 ——
  (function(){
    var it = upItems()[0];
    if(!it){ ck('清单里有可拖的项', false, '清单是空的'); return; }
    var nm = it.querySelector('.nm').textContent.trim();
    var st = stgEl();
    st.scrollIntoView({ block: 'center' });
    var a = center(it.querySelector('.nm'));
    var b = center(st);
    pe('pointerdown', a.x, a.y, it.querySelector('.nm'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    ck('从「未入清单」也能把药拖上台面', stgNames().indexOf(nm) >= 0, nm + ' → ' + stgNames().join('、'));
    ck('拖上去之后那一刻从清单里消失', upNames().indexOf(nm) < 0, upNames().join('、'));
  })();

  // —— 存档：台面跟着存、导入导出不丢、按人分开 ——
  ck('台面内容进了存档（localStorage 里读得到 stage）', (function(){
    try {
      var raw = JSON.parse(localStorage.getItem(LS_DATA));
      var me = raw.people.filter(function(p){ return p.id === currentId; })[0];
      return !!me && Array.isArray(me.stage) && me.stage.length === stageIds().length;
    } catch(e){ return false; }
  })(), stageIds().length + ' 项');

  ck('切到另一个人，台面是空的（按人分开）', (function(){
    var before = stageIds().slice();
    var p2 = addPersonNamed('测试乙');
    var after = stageIds();
    var ok = after.length === 0;
    // 切回来确认原来那份还在
    switchPerson(people.filter(function(p){ return p.stage && p.stage.length; })[0].id);
    return ok && stageIds().join(',') === before.join(',');
  })(), stageIds().length + ' 项');

  // —— 脏数据：老存档没有 stage 字段也要能跑 ——
  ck('老存档（没有 stage 字段）不报错，台面当空的', (function(){
    var p = curPerson();
    delete p.stage;
    ensureStage(p);
    save(); render();
    return Array.isArray(p.stage) && p.stage.length === 0 && !!stgEl();
  })(), '');

  ck('脏数据（重复 id / 非字符串 / 空值）会被洗干净', (function(){
    var p = curPerson();
    var realId = meds[0].id;
    p.stage = [realId, realId, '', null, 123, realId];
    ensureStage(p);
    save(); render();
    return p.stage.length === 1 && p.stage[0] === realId;
  })(), JSON.stringify(stageIds()));

  // —— 药被删掉之后，台面上那一份不该显示成空壳 ——
  (function(){
    var p = curPerson();
    var m = byName('早饭前片');
    if(!m){ ck('有可删的药', false, ''); return; }
    p.stage = [m.id];
    ensureStage(p); save(); render();
    setMeds(meds.filter(function(x){ return x.id !== m.id; }));
    save(); render();
    ck('药被删了，台面上不再显示那个空壳', stgItems().length === 0, stgItems().length);
  })();

  // —— 从格子拖上台面：复制，不动原格（不然"再往别处放一份"就没法做了）——
  (function(){
    var m = byName('晚饭后胶囊');
    dragToListCard('晚饭后胶囊', 0);
    var it = q('#box .slot .chip[data-med="' + m.id + '"]');
    var st = stgEl();
    if(!it){ ck('格子里有可拖的药', false, ''); return; }
    st.scrollIntoView({ block: 'center' });
    var a = center(it.querySelector('.nm'));
    var b = center(st);
    pe('pointerdown', a.x, a.y, it.querySelector('.nm'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    ck('从格子也能把药放上台面', stgNames().indexOf('晚饭后胶囊') >= 0, stgNames().join('、'));
    ck('放上台面之后原来那格还在（是复制不是搬走）', nameIn(0, '晚饭后胶囊'), slotIds(0).length);
    ck('并说明原来那格没动', /原来那格没动/.test(q('#toast').textContent), q('#toast').textContent);
  })();

  // —— 台面上的药拖回「未入清单」= 收走（和点 × 一样）——
  (function(){
    var it = stgItemOf('晚饭后胶囊');
    var up = q('#box .unplaced');
    if(!it || !up){ ck('台面与清单都在', false, ''); return; }
    up.scrollIntoView({ block: 'center' });
    var a = center(it.querySelector('.nm'));
    var b = center(up);
    pe('pointerdown', a.x, a.y, it.querySelector('.nm'));
    pe('pointermove', Math.round((a.x + b.x) / 2), Math.round((a.y + b.y) / 2));
    pe('pointermove', b.x, b.y);
    pe('pointerup', b.x, b.y);
    ck('把台面上的药拖到未入清单 = 收走', stgNames().indexOf('晚饭后胶囊') < 0, stgNames().join('、'));
    ck('收走后台面进入空态', /台面空着/.test(stgEl().textContent), '');
    ck('收走台面不动已经摆好的那一格', nameIn(0, '晚饭后胶囊'), slotIds(0).length);
  })();

  /* ---------------- 20. 通用弹窗本身 ---------------- */
  // 用户 2026-10-23 原话：「点删除之后 应该是弹窗来确认 而不是由页面默认方式来实现
  //   所有的类似的确认弹窗 都应该是独立的弹窗而不是默认」。
  // 下面几条盯的就是"弹窗这件事本身"，而不只是"删没删掉"。
  fixture();

  // 20.1 Esc = 取消（不能拦着不让关，也不能关了就当同意）
  var pdlgEsc = delMed(byName('早饭前片').id);
  ck('删药弹窗开着（准备测 Esc）', q('#dlgAsk').open === true, '');
  q('#dlgAsk').dispatchEvent(new Event('cancel', { cancelable: true }));
  await pdlgEsc;
  ck('★ Esc 关窗 = 取消，药还在', !!byName('早饭前片'), byName('早饭前片') ? '在' : '被删了');
  ck('Esc 之后弹窗真的关掉了', q('#dlgAsk').open === false, q('#dlgAsk').open);

  // 20.2 点遮罩 = 取消
  var pdlgMask = delMed(byName('早饭前片').id);
  q('#dlgAsk').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  await pdlgMask;
  ck('★ 点遮罩 = 取消，药还在', !!byName('早饭前片'), byName('早饭前片') ? '在' : '被删了');

  // 20.3 提示框只有一个按钮，写「知道了」
  var pdlgTip = askAlert({ title: '测试提示', sub: '内容' });
  var tipBtns = qa('#askFoot .btn');
  ck('提示框只有一个按钮', tipBtns.length === 1, tipBtns.length);
  ck('提示框按钮写「知道了」（不是 OK / 确定）',
     tipBtns[0] && tipBtns[0].textContent === '知道了', tipBtns[0] ? tipBtns[0].textContent : '');
  tapAsk('知道了');
  await pdlgTip;
  ck('点完「知道了」提示框关掉了', q('#dlgAsk').open === false, '');

  // 20.4 回车 = 主按钮（输入框那种场景：手不用离开键盘）
  var pdlgEnter = askConfirm({ title: '回车测试', sub: '', okText: '好' });
  q('#formAsk').requestSubmit();
  ck('回车走的是主按钮（拿回 true）', (await pdlgEnter) === true, '');

  // 20.5 连点两次：后开的把前一个当取消结掉，前一个的 await 不会永远悬着
  var pdlgTwice = delMed(byName('早饭前片').id);
  var pdlgTwice2 = askAlert({ title: '第二个', sub: '' });
  tapAsk('知道了');
  await pdlgTwice2;
  var twiceAlive = !!byName('早饭前片');
  await pdlgTwice;
  ck('★ 连开两个弹窗时前一个当"取消"结掉 —— 第二次点删除不会把第一次的意图糊上去',
     twiceAlive, twiceAlive ? '在' : '被删了');

  // 20.6 全程零原生弹窗
  ck('★ 整个测试过程一次都没走浏览器原生 alert / confirm / prompt',
     R.natives.length === 0, R.natives.slice(0, 3).join(' | ') || '(一次都没有)');

  fixture();
  } catch(err){
    // 断言里抛异常（比如去点一个已经不存在的元素）时，别让整个脚本静默死掉 ——
    // 否则 python 那边只会打印"没拿到测试输出"，什么线索都没有。
    R.cases.push({ name: '★ 测试脚本抛异常中断（后面的断言都没跑）', pass: false,
                   got: String((err && err.message) || err) });
  }
  var pre = document.createElement('pre');
  pre.id = '__T';
  pre.textContent = JSON.stringify(R);
  document.body.appendChild(pre);
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
        print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c.get("got")))
    print("\n" + "-" * 60)
    print("通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
