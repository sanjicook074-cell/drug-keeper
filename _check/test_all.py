# -*- coding: utf-8 -*-
"""
drug-keeper：全家汇总（跨人按药名合并 + 调总量）—— 真实 DOM 驱动测试。

用户 2026-10-27 的要求，原话：
    「全家人的所有药合在一起生成一个汇总 只有在人数多于1个的时候出现 和添加人并列
      然后这里面也可以调整药品数量 调整完的药品 如果几个家庭成员都有这个药
      就把总药量的变化分配给有药的成员 让他们的药的天数相同」

★ 但「让天数相同」这半句当天就被用户推翻了，原话：
    「不能搞成一样的 因为开药的时候 可能每个人药量不同 比如a是2片 b是1片
      如果搞成一样的 那开药的时候 开成b a就不够吃了 所以还是改成谁的余量更少 就加给谁」
  现在的口径：多的量**全给余量最少**那位，少的量从**余量最多**那位开始扣。
  本脚本钉的是**现在**的口径 —— 别再照"天数对齐"去改断言。

这一页要回答的问题只有一个：**全家这个药一共还剩多少，调完该补给谁**。
所以本脚本钉这几条：
  ① 只在人数 > 1 时出现，且就在「＋ 添加人」旁边；
  ② 合并口径 = 药名 + 单位（片和粒加不到一起，绝不糊成一行）；
  ③ 天数口径：几个人都一样就写「都够吃 N 天」，不一样就如实写「天数不一（10 / 5）」，
     不许拿平均数糊过去；
  ④ ★ 分配口径：改总量后，多的**全部**加给余量（剩余天数）最少的那一位，其余人一分不动；
     少的从余量最多的那位开始扣，扣光再找下一位，绝不扣成负数；
  ⑤ 备用药 / 没填用法的不参与分配（没有天数这回事），数量原样不动，行里写「调不了」；
  ⑥ 只动这一味药，别处的库存一点不碰。

做法同 test_fam.py：复制 index.html，在主 IIFE 末尾注入探针，无头 Chrome --dump-dom 取回。
★ 必须注入探针：famAllRows / giveDelta / applyAllTotal 都在主 IIFE 内部，外部看不见。
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
/* ===== 注入的测试代码（必须插在主 IIFE 内部，否则 famAllRows / giveDelta 都看不见）===== */
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

  /* 两个人，六种"跨人关系"各占一行：
       同天药片   甲 1片/天 20 片（20 天）· 乙 2片/天 40 片（20 天）→ 天数本来就一样（并列）
       差天药片   甲 1片/天 10 片（10 天）· 乙 2片/天 10 片（5 天）  → 乙吃得多的反而更急
       急用片     甲 3片/天 9 片（3 天）  · 乙 1片/天 5 片（5 天）   → 甲片数多但天数少
       独有药片   只有甲有                                              → 1 人
       同名异单位 甲 10 片 · 乙 6 粒                                    → 必须分成两行
       备用共用药 两人都是备用药                                        → 整行不可调
     ★ 「差天药片」就是用户说的那个场景：「a 是 2 片 b 是 1 片」—— 吃得多的人先不够，
       所以加药必须补给他，而不是两个人一起按比例长。
     ★ 「急用片」专门用来分「按天数」和「按片数」两种比法：甲 9 片比乙 5 片**多**，
       但甲每天 3 片只够 3 天、乙每天 1 片够 5 天。给谁？按天数给甲，按片数给乙。
       用户说的是「余量更少」—— 余量 = 还能吃几天，所以必须给甲。 */
  function fixture(){
    var jia = who('甲', [
      mk('同天药片', { stock: 20, pd: 1, tp: 1 }),
      mk('差天药片', { stock: 10, pd: 1, tp: 1 }),
      mk('急用片', { stock: 9, pd: 3, tp: 1 }),
      mk('独有药片', { stock: 30 }),
      mk('同名异单位', { stock: 10, unit: '片' }),
      mk('备用共用药', { stock: 4, kind: 'prn', tp: 0 })
    ]);
    var yi = who('乙', [
      mk('同天药片', { stock: 40, pd: 1, tp: 2 }),
      mk('差天药片', { stock: 10, pd: 1, tp: 2 }),
      mk('急用片', { stock: 5, pd: 1, tp: 1 }),      // 片数少、天数反而多（5 > 3）
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
  // 这一行里「余量最少」那个小标挂在谁头上 —— 标出来的必须就是加药时真会拿到的那个
  function lowWho(name){
    var r = row1(name);
    if(!r) return '(没有这行)';
    var e = r.querySelector('.all-mem .am-low');
    if(!e) return '(没有标)';
    var b = e.parentNode.querySelector('b');
    return b ? norm(b.textContent) : '?';
  }
  function lowCount(name){ var r = row1(name); return r ? r.querySelectorAll('.am-low').length : -1; }

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
     /2 个人/.test(q('#allSub').textContent) && /共 7 种/.test(q('#allSub').textContent),
     q('#allSub').textContent);

  /* ---------------- 2. 合并口径：药名 + 单位 ---------------- */
  ck('一共 7 行（6 种药 + 同名异单位拆成两行）', rowsAll().length === 7, rowsAll().length);
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

  /* ---------------- 5. ★ 核心：改总量 → 多的全加给「余量最少」那位 ---------------- */
  ck('行上有「调总量」按钮', !!row1('同天药片').querySelector('[data-alladj]'), '');

  // 先只看弹窗长什么样、不提交：标题点不点名、预填对不对、说明有没有把分配规则讲明白
  row1('同天药片').querySelector('[data-alladj]').click();
  ck('点「调总量」弹的是页面内的数字弹窗（不是原生 prompt）',
     !!(q('#dlgNum') && q('#dlgNum').open), q('#dlgNum').open);
  ck('弹窗标题点名是哪一味药', /同天药片/.test(q('#numTitle').textContent),
     q('#numTitle').textContent);
  ck('输入框预填现在的总量 60（不用自己算）', q('#numVal').value === '60', q('#numVal').value);
  ck('★ 说明里点明「多出来的全加给谁」—— 不再说"按比例分、天数一样"',
     /全加给/.test(q('#numSub').textContent) && !/天数一样/.test(q('#numSub').textContent),
     q('#numSub').textContent);
  ck('说明里列出各人现在的数量（甲 20 + 乙 40）',
     /甲 20/.test(q('#numSub').textContent) && /乙 40/.test(q('#numSub').textContent),
     q('#numSub').textContent);
  q('#btnNumCancel').click();
  ck('点取消：弹窗关掉、数字一个都没动',
     !q('#dlgNum').open && totalOf(row1('同天药片')) === 60 && stockOf(F.jia, '同天药片') === 20,
     q('#dlgNum').open + ' / ' + totalOf(row1('同天药片')));

  // 「余量最少」这个标不是装点：加药时就落在这个人身上，标错了药就补错人
  ck('★ 明细里给「余量最少」的那位挂了小标（20 天并列 → 标在排前面的甲）',
     lowWho('同天药片') === '甲', lowWho('同天药片'));
  ck('★ 每行只标一个人（标两个等于没标）', lowCount('同天药片') === 1, lowCount('同天药片'));
  ck('单人那行不挂这个标（只有一个人在吃，谈不上"最少"）',
     lowCount('独有药片') === 0, lowCount('独有药片'));

  var st = adj('同天药片', 90);
  ck('填 90 提交后弹窗自己关掉', st === 'ok' && q('#dlgNum').open === false, st);
  ck('★ 多的 30 片全落到甲头上（20 → 50）', stockOf(F.jia, '同天药片') === 50,
     stockOf(F.jia, '同天药片'));
  ck('★ 乙一分没动，还是原来的 40 片 —— 不再是"按比例各长一截"',
     stockOf(F.yi, '同天药片') === 40, stockOf(F.yi, '同天药片'));
  ck('加起来正好等于 90（一粒都没漏）',
     round2(stockOf(F.jia, '同天药片') + stockOf(F.yi, '同天药片')) === 90,
     round2(stockOf(F.jia, '同天药片') + stockOf(F.yi, '同天药片')));
  ck('★ 分完两个人的天数**不再相同**（甲 50 天 / 乙 20 天）—— 这是刻意的',
     dl(F.jia, '同天药片') === 50 && dl(F.yi, '同天药片') === 20,
     dl(F.jia, '同天药片') + ' / ' + dl(F.yi, '同天药片'));
  ck('改完基准日滚到了今天（和「校准余量」同一套口径）',
     medOf(F.jia, '同天药片').stockDate === todayStr() &&
     medOf(F.yi, '同天药片').stockDate === todayStr(),
     medOf(F.jia, '同天药片').stockDate + ' / ' + medOf(F.yi, '同天药片').stockDate);
  ck('页面如实写「天数不一（50 / 20）」',
     /天数不一（50 \/ 20）/.test(metaOf(row1('同天药片'))), metaOf(row1('同天药片')));
  ck('★ 标跟着挪到乙身上了（现在乙 20 天 < 甲 50 天）',
     lowWho('同天药片') === '乙', lowWho('同天药片'));
  ck('★ 点完有个轻提示说清"给了谁"（不然另一人没变，看着像没生效）',
     /30 片 全加给了 甲/.test(q('#toast').textContent), q('#toast').textContent);
  ck('★ 只动这一味药：别人的「独有药片」还是 30 片',
     stockOf(F.jia, '独有药片') === 30, stockOf(F.jia, '独有药片'));
  ck('别的药也没被顺带改（差天药片还是 10 / 10）',
     stockOf(F.jia, '差天药片') === 10 && stockOf(F.yi, '差天药片') === 10,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));
  ck('备用药一点没碰（甲 4 / 乙 6）',
     stockOf(F.jia, '备用共用药') === 4 && stockOf(F.yi, '备用共用药') === 6,
     stockOf(F.jia, '备用共用药') + ' / ' + stockOf(F.yi, '备用共用药'));

  /* ---------------- 6. ★ 用户那个场景：a 每天 2 片、b 每天 1 片 ---------------- */
  // 现在 甲 10 片（1 片/天 · 10 天）· 乙 10 片（2 片/天 · 5 天），共 20
  adj('差天药片', 40);                       // Δ = +20
  ck('★ 多的 20 片全给乙（吃得快，只剩 5 天）：10 → 30 片',
     stockOf(F.yi, '差天药片') === 30, stockOf(F.yi, '差天药片'));
  ck('★ 甲一片没动，还是 10 片 —— 吃得多的人先补，这正是用户要的',
     stockOf(F.jia, '差天药片') === 10, stockOf(F.jia, '差天药片'));
  ck('★ 天数没被"抹平"：甲 10 天 / 乙 15 天',
     dl(F.jia, '差天药片') === 10 && dl(F.yi, '差天药片') === 15,
     dl(F.jia, '差天药片') + ' / ' + dl(F.yi, '差天药片'));
  ck('加起来正好 40',
     round2(stockOf(F.jia, '差天药片') + stockOf(F.yi, '差天药片')) === 40,
     round2(stockOf(F.jia, '差天药片') + stockOf(F.yi, '差天药片')));

  /* ---------------- 6b. 「余量」按天数比，不按片数 ---------------- */
  // 急用片：甲 9 片（3 片/天 = 3 天）· 乙 5 片（1 片/天 = 5 天），共 14
  ck('★ 甲片数更多（9 > 5）但天数更少（3 < 5）→ 标在甲身上',
     lowWho('急用片') === '甲', lowWho('急用片'));
  adj('急用片', 20);                         // Δ = +6
  ck('★ 多的 6 片给甲（他只剩 3 天），不是给片数更少的乙',
     stockOf(F.jia, '急用片') === 15 && stockOf(F.yi, '急用片') === 5,
     stockOf(F.jia, '急用片') + ' / ' + stockOf(F.yi, '急用片'));

  /* ---------------- 7. 减药：从「余量最多」那位开始扣，扣光再找下一位 ---------------- */
  // 现在 甲 10 片（10 天）· 乙 30 片（15 天），共 40
  adj('差天药片', 35);                       // Δ = -5
  ck('★ 减 5 片：从乙（余量最多）扣，甲还是 10',
     stockOf(F.jia, '差天药片') === 10 && stockOf(F.yi, '差天药片') === 25,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));
  ck('减药也有提示，写清是从谁那儿扣的',
     /从 乙 扣了 5 片/.test(q('#toast').textContent), q('#toast').textContent);
  adj('差天药片', 8);                        // 共 35 → 8，Δ = -27
  ck('★ 乙的 25 片全扣光还不够，接着从甲扣 2 片（甲 8 / 乙 0）',
     stockOf(F.jia, '差天药片') === 8 && stockOf(F.yi, '差天药片') === 0,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));
  ck('★ 一次动了两个人时，两笔都报出来（甲 −2，乙 −25）',
     /甲 −2/.test(q('#toast').textContent) && /乙 −25/.test(q('#toast').textContent),
     q('#toast').textContent);
  ck('★ 谁都没被扣成负数（余量不会反向变多）',
     stockOf(F.jia, '差天药片') >= 0 && stockOf(F.yi, '差天药片') >= 0,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));
  adj('差天药片', 0);
  ck('填 0 = 全用完：两个人都归零，总量守恒',
     stockOf(F.jia, '差天药片') === 0 && stockOf(F.yi, '差天药片') === 0,
     stockOf(F.jia, '差天药片') + ' / ' + stockOf(F.yi, '差天药片'));

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

  /* ---------------- 10. giveDelta：纯函数，把边界钉死 ---------------- */
  // 造一个"成员"给纯函数用：giveDelta 只看 stock（今天剩多少）和 dl（够几天）
  function px(stock, daily){
    return { stock: round2(stock), daily: daily, dl: round2(stock / daily) };
  }
  ck('总量本来就是这个数 → 谁都不动（60 → [20,40]）',
     JSON.stringify(giveDelta([px(20,1), px(40,2)], 60)) === '[20,40]',
     JSON.stringify(giveDelta([px(20,1), px(40,2)], 60)));
  ck('★ 加到 90：多出来的 30 全给第一位，第二位一分不动',
     JSON.stringify(giveDelta([px(20,1), px(40,2)], 90)) === '[50,40]',
     JSON.stringify(giveDelta([px(20,1), px(40,2)], 90)));
  ck('★ 按天数不按片数：9 片(3天) / 5 片(5天) 加 6 → 给 9 片那位',
     JSON.stringify(giveDelta([px(9,3), px(5,1)], 20)) === '[15,5]',
     JSON.stringify(giveDelta([px(9,3), px(5,1)], 20)));
  (function(){
    var s = giveDelta([px(10,1), px(20,1), px(30,1)], 90);
    ck('★ 三个人：多的 30 全给第一个（三个人天数并列）',
       JSON.stringify(s) === '[40,20,30]', JSON.stringify(s));
  })();
  ck('减 20：从余量最多的那位扣，另一位不动',
     JSON.stringify(giveDelta([px(10,1), px(30,1)], 20)) === '[10,10]',
     JSON.stringify(giveDelta([px(10,1), px(30,1)], 20)));
  (function(){
    var s = giveDelta([px(10,1), px(30,1)], 5);
    ck('★ 减到需要两个人：先扣光 30 那位，再扣 10 那位 5 片',
       JSON.stringify(s) === '[5,0]', JSON.stringify(s));
  })();
  ck('总量给 0 → 全员归零', JSON.stringify(giveDelta([px(10,1), px(30,1)], 0)) === '[0,0]',
     JSON.stringify(giveDelta([px(10,1), px(30,1)], 0)));
  ck('空清单（没人吃这个药）→ 空数组，不会炸',
     JSON.stringify(giveDelta([], 7)) === '[]', JSON.stringify(giveDelta([], 7)));
  (function(){
    var pool = [px(20,1), px(40,2)];
    giveDelta(pool, 90);
    ck('★ 纯函数：不改调用方传进来的对象（算之前得自己快照，不然会出现两个数都不对）',
       pool[0].stock === 20 && pool[1].stock === 40,
       pool[0].stock + ' / ' + pool[1].stock);
  })();

  /* ---------------- 11. 存盘：调完真的写进 localStorage 了 ---------------- */
  (function(){
    var raw = localStorage.getItem('medkeeper.data.v1');
    var ok = false, v = '(读不到存档)';
    try{
      var o = JSON.parse(raw);
      var p = o.people.filter(function(x){ return x.id === F.jia.id; })[0];
      var m = p.meds.filter(function(x){ return x.name === '同天药片'; })[0];
      v = m.stockBase; ok = (m.stockBase === 50);   // 第 5 段把「同天药片」的甲从 20 加到了 50
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
