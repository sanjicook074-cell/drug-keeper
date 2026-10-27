# -*- coding: utf-8 -*-
"""
drug-keeper：全家汇总（跨人按药名合并 + 调总量）—— 真实 DOM 驱动测试。

用户 2026-10-27 的要求，原话：
    「全家人的所有药合在一起生成一个汇总 只有在人数多于1个的时候出现 和添加人并列
      然后这里面也可以调整药品数量 调整完的药品 如果几个家庭成员都有这个药
      就把总药量的变化分配给有药的成员 让他们的药的天数相同」

这一页要回答的问题只有一个：**全家这个药一共还剩多少，调完够大家吃几天**。
所以本脚本钉这几条：
  ① 只在人数 > 1 时出现，且就在「＋ 添加人」旁边；
  ② 合并口径 = 药名 + 单位（片和粒加不到一起，绝不糊成一行）；
  ③ 天数口径：几个人都一样就写「都够吃 N 天」，不一样就如实写「天数不一（10 / 5）」，
     不许拿平均数糊过去；
  ④ ★ 分配口径：改总量后按各人「每天用量」的比例分 —— 分完每人的天数**必须相同**；
  ⑤ 备用药 / 没填用法的不参与分配（没有天数这回事），数量原样不动，行里写「调不了」；
  ⑥ 只动这一味药，别处的库存一点不碰。

做法同 test_fam.py：复制 index.html，在主 IIFE 末尾注入探针，无头 Chrome --dump-dom 取回。
★ 必须注入探针：famAllRows / splitTotal / applyAllTotal 都在主 IIFE 内部，外部看不见。
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
/* ===== 注入的测试代码（必须插在主 IIFE 内部，否则 famAllRows / splitTotal 都看不见）===== */
(function(){
  var R = { cases: [] };
  window.__T = R;
  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function agoD(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); }

  // 造一味药：stock 就是「今天还剩多少」（stockDate 今天是 → 剩余天数 = stock / 每天用量）
  function mk(name, o){
    o = o || {};
    return {
      id: uid(), name: name, spec: '',
      perDose: (o.pd == null ? 1 : o.pd),
      unit: o.unit || '片',
      cycleDays: o.cd || 1,
      timesPerCycle: (o.tp == null ? 1 : o.tp),
      stockBase: (o.stock == null ? 30 : o.stock),
      stockDate: agoD(o.ago || 0),
      rxMax: 30,
      kind: o.kind || 'daily'
    };
  }
  function who(name, meds){
    return { id: uid(), name: name, note: '', box: { slots: defaultSlots(3) }, meds: meds };
  }

  /* 两个人，五种"跨人关系"各占一行：
       同天药片   甲 1片/天 20 片（20 天）· 乙 2片/天 40 片（20 天）→ 天数本来就一样
       差天药片   甲 1片/天 10 片（10 天）· 乙 2片/天 10 片（5 天）  → 天数不一样
       独有药片   只有甲有                                              → 1 人
       同名异单位 甲 10 片 · 乙 6 粒                                    → 必须分成两行
       备用共用药 两人都是备用药                                        → 整行不可调 */
  function fixture(){
    var jia = who('甲', [
      mk('同天药片', { stock: 20, pd: 1, tp: 1 }),
      mk('差天药片', { stock: 10, pd: 1, tp: 1 }),
      mk('独有药片', { stock: 30 }),
      mk('同名异单位', { stock: 10, unit: '片' }),
      mk('备用共用药', { stock: 4, kind: 'prn', tp: 0 })
    ]);
    var yi = who('乙', [
      mk('同天药片', { stock: 40, pd: 1, tp: 2 }),
      mk('差天药片', { stock: 10, pd: 1, tp: 2 }),
      mk('同名异单位', { stock: 6, unit: '粒' }),
      mk('备用共用药', { stock: 6, kind: 'prn', tp: 0 })
    ]);
    people.length = 0;
    people.push(jia, yi);
    currentId = jia.id;
    syncMeds(); save(); render();
    return { jia: jia, yi: yi };
  }
  function medOf(p, name){ return p.meds.filter(function(m){ return m.name === name; })[0]; }
  function rowsAll(){ return qa('#allList .disp-row'); }
  function rowOf(name){
    return rowsAll().filter(function(r){
      var e = r.querySelector('.fam-mn');
      return e && e.textContent.trim() === name;
    });
  }
  function row1(name){ return rowOf(name)[0]; }
  function norm(t){ return String(t).replace(/\s+/g, ' ').trim(); }
  function metaOf(r){ return norm(r.querySelector('.disp-meta').textContent); }
  function memOf(r){ return norm(r.querySelector('.all-mem').textContent); }
  function peopleN(r){ return norm(r.querySelector('.all-n').textContent); }
  function totalOf(r){
    var m = metaOf(r).match(/总量\s*([\d.]+)/);
    return m ? Number(m[1]) : NaN;
  }
  // 点这一行的「调总量」，填数，提交（走页面真的 submit 事件，不直接调 applyAllTotal）
  function adj(name, val, idx){
    var rs = rowOf(name);
    var r = rs[idx || 0];
    if(!r) return 'no-row';
    var b = r.querySelector('[data-alladj]');
    if(!b) return 'no-button';
    b.click();
    if(!q('#dlgNum').open) return 'dialog-not-open';
    q('#numVal').value = String(val);
    q('#formNum').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    return 'ok';
  }
  function dl(p, name){ var m = medOf(p, name); return m ? daysLeft(m) : NaN; }
  function stockOf(p, name){ var m = medOf(p, name); return m ? stockNow(m) : NaN; }

  /* ---------------- 1. 入口：只在人多于一的时候出现，且跟「添加人」并排 ---------------- */
  var F = fixture();
  var addBtn = q('#btnAddPerson'), allBtn = q('#btnAll');
  ck('两个人时出现了「全家汇总」', !!allBtn && /全家汇总/.test(allBtn.textContent),
     allBtn ? allBtn.textContent : '(没有这个按钮)');
  ck('★ 它就跟在「＋ 添加人」后面（并列，不是在顶栏另开一处）',
     !!addBtn && addBtn.nextElementSibling === allBtn,
     addBtn ? String(addBtn.nextElementSibling && addBtn.nextElementSibling.id) : '(没有添加人)');
  ck('都是同一个人员栏里的胶囊按钮（样式一致）',
     !!allBtn && allBtn.className === addBtn.className, allBtn ? allBtn.className : '');
  ck('一开始弹窗是关着的', !q('#dlgAll').open, q('#dlgAll').open);
  allBtn.click();
  ck('点一下打开了全家汇总', q('#dlgAll').open === true, q('#dlgAll').open);
  ck('副标题说清是几个人、合并成几种',
     /2 个人/.test(q('#allSub').textContent) && /共 6 种/.test(q('#allSub').textContent),
     q('#allSub').textContent);

  /* ---------------- 2. 合并口径：药名 + 单位 ---------------- */
  ck('一共 6 行（5 种药 + 同名异单位拆成两行）', rowsAll().length === 6, rowsAll().length);
  ck('两个人都有「同天药片」→ 合并成一行、标「2 人」',
     rowOf('同天药片').length === 1 && peopleN(row1('同天药片')) === '2 人',
     rowOf('同天药片').length + ' 行 / ' + peopleN(row1('同天药片')));
  ck('★ 总量 = 两人余量之和（20 + 40 = 60）', totalOf(row1('同天药片')) === 60,
     totalOf(row1('同天药片')));
  ck('明细里两个人都写出来了（甲也写了、乙也写了）',
     /甲/.test(memOf(row1('同天药片'))) && /乙/.test(memOf(row1('同天药片'))),
     memOf(row1('同天药片')));
  ck('只有一个人有的药标「1 人」', peopleN(row1('独有药片')) === '1 人',
     peopleN(row1('独有药片')));
  ck('★ 同名但单位不同（片 / 粒）分成两行，绝不加到一起',
     rowOf('同名异单位').length === 2, rowOf('同名异单位').length + ' 行');
  ck('这两行的单位分别写对了',
     /10 片/.test(memOf(rowOf('同名异单位')[0])) && /6 粒/.test(memOf(rowOf('同名异单位')[1])),
     memOf(rowOf('同名异单位')[0]) + ' || ' + memOf(rowOf('同名异单位')[1]));

  /* ---------------- 3. 天数口径：一样就说一样，不一样就如实说 ---------------- */
  ck('天数本来就一样 → 写「都够吃 20 天」',
     /都够吃 20 天/.test(metaOf(row1('同天药片'))), metaOf(row1('同天药片')));
  ck('★ 天数不一样 → 如实写「天数不一（10 / 5）」，不拿平均数糊过去',
     /天数不一（10 \/ 5）/.test(metaOf(row1('差天药片'))), metaOf(row1('差天药片')));
  ck('单人那行写「够吃 30 天」',
     /够吃 30 天/.test(metaOf(row1('独有药片'))), metaOf(row1('独有药片')));

  /* ---------------- 4. 备用药不参与分配 ---------------- */
  var prnRow = row1('备用共用药');
  ck('备用药也列在这一页（这一页是"所有药"）', !!prnRow, prnRow ? '在' : '(漏了)');
  ck('但写明不按天算', /不按天算/.test(metaOf(prnRow)), metaOf(prnRow));
  ck('★ 整行不可调：没有「调总量」按钮，只有「调不了」',
     !prnRow.querySelector('[data-alladj]') && /调不了/.test(prnRow.textContent),
     prnRow.querySelector('[data-alladj]') ? '竟然有按钮' : '调不了');
  ck('两人的备用药数量都如实写出来（4 片 / 6 片）',
     /4 片/.test(memOf(prnRow)) && /6 片/.test(memOf(prnRow)), memOf(prnRow));

  /* ---------------- 5. ★ 核心：改总量 → 按每天用量分 → 天数相同 ---------------- */
  ck('行上有「调总量」按钮', !!row1('同天药片').querySelector('[data-alladj]'), '');

  // 先只看弹窗长什么样、不提交：标题点不点名、预填对不对、说明有没有把分配规则讲明白
  row1('同天药片').querySelector('[data-alladj]').click();
  ck('点「调总量」弹的是页面内的数字弹窗（不是原生 prompt）',
     !!(q('#dlgNum') && q('#dlgNum').open), q('#dlgNum').open);
  ck('弹窗标题点名是哪一味药', /同天药片/.test(q('#numTitle').textContent),
     q('#numTitle').textContent);
  ck('输入框预填现在的总量 60（不用自己算）', q('#numVal').value === '60', q('#numVal').value);
  ck('★ 说明里把分配规则讲明白了（按每天用量分、分完天数一样）',
     /每天用量/.test(q('#numSub').textContent) && /天数一样/.test(q('#numSub').textContent),
     q('#numSub').textContent);
  ck('说明里列出各人现在的数量（甲 20 + 乙 40）',
     /甲 20/.test(q('#numSub').textContent) && /乙 40/.test(q('#numSub').textContent),
     q('#numSub').textContent);
  q('#btnNumCancel').click();
  ck('点取消：弹窗关掉、数字一个都没动',
     !q('#dlgNum').open && totalOf(row1('同天药片')) === 60 && stockOf(F.jia, '同天药片') === 20,
     q('#dlgNum').open + ' / ' + totalOf(row1('同天药片')));

  var st = adj('同天药片', 90);
  ck('填 90 提交后弹窗自己关掉', st === 'ok' && q('#dlgNum').open === false, st);
  ck('★ 总量改成 90：甲按 1 片/天分到 30 片', stockOf(F.jia, '同天药片') === 30,
     stockOf(F.jia, '同天药片'));
  ck('★ 乙按 2 片/天分到 60 片', stockOf(F.yi, '同天药片') === 60,
     stockOf(F.yi, '同天药片'));
  ck('★ 分完两个人的天数完全相同（各 30 天）',
     dl(F.jia, '同天药片') === 30 && dl(F.yi, '同天药片') === 30,
     dl(F.jia, '同天药片') + ' / ' + dl(F.yi, '同天药片'));
  ck('加起来正好等于 90（没有零头漏掉）',
     round2(stockOf(F.jia, '同天药片') + stockOf(F.yi, '同天药片')) === 90,
     round2(stockOf(F.jia, '同天药片') + stockOf(F.yi, '同天药片')));
  ck('分完之后基准日滚到了今天（和「校准余量」同一套口径）',
     medOf(F.jia, '同天药片').stockDate === todayStr() &&
     medOf(F.yi, '同天药片').stockDate === todayStr(),
     medOf(F.jia, '同天药片').stockDate + ' / ' + medOf(F.yi, '同天药片').stockDate);
  ck('页面跟着刷新成「都够吃 30 天」',
     /都够吃 30 天/.test(metaOf(row1('同天药片'))), metaOf(row1('同天药片')));
  ck('★ 只动这一味药：别人的「独有药片」还是 30 片',
     stockOf(F.jia, '独有药片') === 30, stockOf(F.jia, '独有药片'));
  ck('别的药也没被顺带改（差天药片还是 10 / 10）',
     stockOf(F.jia, '差天药片') === 10 && stockOf(F.yi, '差天药片') === 10,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));
  ck('备用药一点没碰（甲 4 / 乙 6）',
     stockOf(F.jia, '备用共用药') === 4 && stockOf(F.yi, '备用共用药') === 6,
     stockOf(F.jia, '备用共用药') + ' / ' + stockOf(F.yi, '备用共用药'));

  /* ---------------- 6. 天数本来就不一样的也能对齐 ---------------- */
  adj('差天药片', 30);
  ck('★ 差天药片改成 30：甲 10 片 / 乙 20 片',
     stockOf(F.jia, '差天药片') === 10 && stockOf(F.yi, '差天药片') === 20,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));
  ck('★ 对完之后两人各 10 天（原来 10 天 / 5 天）',
     dl(F.jia, '差天药片') === 10 && dl(F.yi, '差天药片') === 10,
     dl(F.jia, '差天药片') + ' / ' + dl(F.yi, '差天药片'));

  /* ---------------- 7. 除不尽的时候：总量守恒、天数仍然一样 ---------------- */
  adj('差天药片', 10);
  var a10 = stockOf(F.jia, '差天药片'), b10 = stockOf(F.yi, '差天药片');
  ck('★ 10 片按 1:2 除不尽 → 3.33 / 6.67，加起来仍是 10',
     a10 === 3.33 && b10 === 6.67 && round2(a10 + b10) === 10,
     a10 + ' + ' + b10 + ' = ' + round2(a10 + b10));
  ck('★ 除不尽时天数仍然对齐（差 < 0.01 天）',
     Math.abs(dl(F.jia, '差天药片') - dl(F.yi, '差天药片')) < 0.01,
     dl(F.jia, '差天药片') + ' vs ' + dl(F.yi, '差天药片'));
  ck('页面写「都够吃 3 天」（吃不满一天不算一天）',
     /都够吃 3 天/.test(metaOf(row1('差天药片'))), metaOf(row1('差天药片')));

  /* ---------------- 8. 只有一个人有的药，也能在这一页调 ---------------- */
  adj('独有药片', 5);
  ck('单人那行改总量 = 直接改 TA 的余量', stockOf(F.jia, '独有药片') === 5,
     stockOf(F.jia, '独有药片'));
  ck('改完这行写「够吃 5 天」', /够吃 5 天/.test(metaOf(row1('独有药片'))),
     metaOf(row1('独有药片')));

  /* ---------------- 9. 写成 0：允许（等于全用完） ---------------- */
  adj('独有药片', 0);
  ck('填 0 = 全用完，不报错', stockOf(F.jia, '独有药片') === 0,
     stockOf(F.jia, '独有药片'));
  ck('这时候档位变成「已用完」', /已用完/.test(metaOf(row1('独有药片'))) ||
     !!row1('独有药片').querySelector('.tag.out'), metaOf(row1('独有药片')));

  /* ---------------- 10. splitTotal：纯函数，把边界钉死 ---------------- */
  ck('splitTotal(90,[1,2]) = 30 / 60',
     JSON.stringify(splitTotal(90, [1, 2])) === '[30,60]',
     JSON.stringify(splitTotal(90, [1, 2])));
  (function(){
    var s = splitTotal(10, [1, 1, 1]);
    ck('★ 三个人平分 10：零头挂到第一个上，加起来正好 10',
       round2(s[0] + s[1] + s[2]) === 10 && s[1] === s[2],
       JSON.stringify(s) + ' → ' + round2(s[0] + s[1] + s[2]));
  })();
  ck('总量 0 → 全 0', JSON.stringify(splitTotal(0, [1, 2])) === '[0,0]',
     JSON.stringify(splitTotal(0, [1, 2])));
  ck('没有权重（全是备用药）→ 全 0，不会除零炸掉',
     JSON.stringify(splitTotal(7, [0, 0])) === '[0,0]',
     JSON.stringify(splitTotal(7, [0, 0])));
  (function(){
    var s = splitTotal(50, [1, 4]);
    ck('权重大的分到的多（1:4 → 10 / 40）', s[0] === 10 && s[1] === 40, JSON.stringify(s));
  })();

  /* ---------------- 11. 存盘：调完真的写进 localStorage 了 ---------------- */
  (function(){
    var raw = localStorage.getItem('medkeeper.data.v1');
    var ok = false, v = '(读不到存档)';
    try{
      var o = JSON.parse(raw);
      var p = o.people.filter(function(x){ return x.id === F.jia.id; })[0];
      var m = p.meds.filter(function(x){ return x.name === '同天药片'; })[0];
      v = m.stockBase; ok = (m.stockBase === 30);
    }catch(e){ v = '(解析失败: ' + e + ')'; }
    ck('★ 调完已经存盘（关掉页面再打开不会退回去）', ok, v);
  })();

  /* ---------------- 12. 空态 + 关闭 + 一个人时的防御 ---------------- */
  people.length = 0;
  people.push(who('甲', []), who('乙', []));
  currentId = people[0].id;
  syncMeds(); save(); render();
  q('#btnAll').click();
  ck('两个人但都没药 → 说清"还没有记录"，不是一片空白',
     /还没有记录任何药品/.test(norm(q('#allList').textContent)),
     norm(q('#allList').textContent));
  ck('空的时候不留那条解释脚注', q('#allNote').textContent === '', q('#allNote').textContent);
  q('#btnAllClose').click();
  ck('点「关闭」真的关掉', q('#dlgAll').open === false, q('#dlgAll').open);

  // 只剩一个人：入口消失，openAll() 就算被硬调也不开（两层拦）
  people.length = 0;
  people.push(who('只有我', [ mk('孤单药片', { stock: 9 }) ]));
  currentId = people[0].id;
  syncMeds(); save(); render();
  ck('★ 只剩一个人时「全家汇总」这个入口消失了', !q('#btnAll'), !!q('#btnAll'));
  ck('「＋ 添加人」还在（它才是常驻的那个）', !!q('#btnAddPerson'), '');
  openAll();
  ck('★ 一个人时硬调 openAll() 也打不开这页', q('#dlgAll').open === false, q('#dlgAll').open);

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
    tmpdir = tempfile.mkdtemp(prefix="dk_all_")
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
