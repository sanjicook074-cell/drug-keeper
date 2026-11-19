# -*- coding: utf-8 -*-
"""
drug-keeper：医保锁 —— 真实 DOM 驱动测试。

用户 2026-10-28 的要求，原话：
    「如果是走医保开的药 那一段时间内这个药品是不能重复用医保开的 所以所有添加药品的地方
      包括底方录入 添加药品 还有又买了药 针对每一种药品数量增加的情形 还需要区分出这个药是
      自费还是走的医保 如果是走的医保 那这个药品还需要有一个上锁的标志 只有在达到下一次开药
      前7天（不同地方天数不同 这个要在设置里由用户填写）这个锁的标志才能被解开」

拆成五件事，本脚本逐条钉住：
  ① 三个"加药"的入口都能分出**自费 / 医保**（添加药品、又买了药、底方录入），另外
     「已经开药」也是去医院开药的动作，默认按医保记；
  ② 医保的上一把锁，锁到哪天是**算**出来的（开药日 + 间隔 − 提前量），不存"锁没锁"；
  ③ 两个天数在**设置**里填，改一次全局生效；
  ④ 锁着自己的开了，卡片上从「🔒 医保 / 锁到几号」变成「医保 / 已经可以再开」；
  ⑤ **不是一次开药的操作不动锁**（校准余量）；顺手验「已经开药只动勾上的那几味」。

★ 零迁移：老存档没有 ins 字段 → 一律当自费、不上锁。下面第 0 节专门钉这一条 ——
  要是给历史数据凭空上锁，用户一打开页面会发现整箱药"都不能开了"。

做法同 test_box.py：复制 index.html → 在主 IIFE 末尾注入探针 → 无头 Chrome --dump-dom。
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
(async function(){
  var R = { alerts: [], cases: [], natives: [] };
  window.__T = R;
  window.alert = function(m){ R.natives.push('alert: ' + String(m)); };
  window.confirm = function(m){ R.natives.push('confirm: ' + String(m)); return true; };
  window.prompt = function(m){ R.natives.push('prompt: ' + String(m)); return ''; };
  // 提示框只记文案，不真弹 —— 这一套测的是业务规则，弹窗机制由 test_box.py 钉
  askAlert = function(o){
    R.alerts.push(typeof o === 'string' ? o : ((o.title || '') + ' ' + (o.sub || '')));
    return Promise.resolve();
  };
  askConfirm = function(o){ return Promise.resolve(true); };
  askChoice = function(o){ return Promise.resolve(o.choices[0].value); };

  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function plusD(n){ var d = new Date(); d.setDate(d.getDate() + n); return fmtDate(d); }
  function agoD(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); }
  function byName(n){
    for(var i = 0; i < meds.length; i++) if(meds[i].name === n) return meds[i];
    return null;
  }
  function cardOf(n){
    var cs = qa('#list .med'), i;
    for(i = 0; i < cs.length; i++){
      var el = cs[i].querySelector('.med-name');
      if(el && el.textContent.indexOf(n) >= 0) return cs[i];
    }
    return null;
  }
  function metaOf(n){
    var c = cardOf(n);
    return c ? c.querySelector('.med-meta').textContent.replace(/\s+/g, ' ').trim() : '(无卡片)';
  }
  // 清空药箱 + 回到默认设置（每个小节都从干净状态起，免得互相污染）
  function reset(){
    setMeds([]);
    settings.alertDays = 14; settings.targetDays = 30;
    settings.miDays = 30;    settings.miEarly = 7;
    saveSettings(); save(); render();
  }
  // 造一味药（直接进数据，不走表单）
  function mk(o){
    var m = {
      id: uid(), kind: o.kind || 'daily',
      name: o.name, spec: '5mg', perDose: o.perDose || 1, unit: '片',
      cycleDays: 1, timesPerCycle: (o.kind === 'prn') ? 0 : (o.times || 1),
      stockBase: (o.stock == null ? 30 : o.stock), stockDate: agoD(o.ago || 0),
      rxMax: 30, rxMaxDay: 1, note: ''
    };
    if(o.rxMax != null) m.rxMax = o.rxMax;          // null/undefined = 用默认 30；0 = 真的没填
    if(o.ins) m.ins = o.ins;
    if(o.miStart) m.miStart = o.miStart;
    meds.push(m); save(); render();
    return m;
  }
  // 走真实的「添加药品」表单
  async function addViaForm(o){
    openEditor(null);
    var f = q('#formMed');
    f.kind.value = o.kind || 'daily';
    f.name.value = o.name;
    f.spec.value = '5mg';
    f.perDose.value = o.perDose || 1;
    f.unit.value = '片';
    f.cycleDays.value = 1;
    f.timesPerCycle.value = o.times || 1;
    f.stockBase.value = (o.stock == null ? 30 : o.stock);
    // 2026-10-28 起「余量是哪天的」不再让人填，表单里没有这一格了（基准日 = 今天）
    f.rxMax.value = 30;
    f.ins.value = o.ins || 'self';
    if(o.miStart) f.miStart.value = o.miStart;
    await submitMed({ preventDefault: function(){} });
  }
  // 走真实的「又买了药」（#dlgNum + 来源选择）
  async function restock(id, num, ins){
    restockMed(id);
    await null;
    var box = q('#numIns');
    var shown = !!(box && !box.hidden);
    q('#numVal').value = String(num);
    var r = box ? box.querySelector('input[value="' + (ins === 'mi' ? 'mi' : 'self') + '"]') : null;
    if(r) r.checked = true;
    syncNumIns();
    var tip = q('#numInsTip') ? q('#numInsTip').textContent : '';
    await submitNum({ preventDefault: function(){} });
    return { shown: shown, tip: tip };
  }

  /* ================= 0. 零迁移：老存档没有 ins 字段 ================= */
  reset();
  var old = mk({ name: '老存档片' });               // 完全没有 ins / miStart
  ck('★ 老存档没有 ins 字段 → 不当成医保（miIsMI=false）', miIsMI(old) === false, miIsMI(old));
  ck('★ 老存档不会被上锁', miLocked(old) === false, miLocked(old));
  ck('老存档的解锁日是空（没记录就没什么可算）', miUnlockDate(old) === '', miUnlockDate(old));
  ck('老存档卡片上没有医保小标', cardOf('老存档片').querySelector('.ins-lock') === null,
     cardOf('老存档片').querySelector('.med-name').textContent.replace(/\s+/g, ' ').trim());
  ck('老存档卡片 meta 里没有「医保」字样', metaOf('老存档片').indexOf('医保') < 0, metaOf('老存档片'));

  /* ================= 1. 锁是怎么算出来的 ================= */
  reset();
  var mi = mk({ name: '医保甲片', ins: 'mi', miStart: todayStr(), stock: 3 });
  ck('默认间隔 30 天（设置里没改过）', miDays() === 30, miDays());
  ck('默认提前 7 天解锁', miEarly() === 7, miEarly());
  ck('今天开的 → 解锁日 = 今天 + 30 − 7', miUnlockDate(mi) === plusD(23), miUnlockDate(mi));
  ck('今天开的 → 现在锁着', miLocked(mi) === true, miLocked(mi));
  ck('今天开的 → 还有 23 天解锁', miDaysToUnlock(miUnlockDate(mi)) === 23,
     miDaysToUnlock(miUnlockDate(mi)));

  // 间隔改大 → 所有已有的锁跟着延长（政策变了改一次就够）
  settings.miDays = 60; saveSettings(); render();
  ck('间隔改成 60 → 解锁日变成 今天 + 53', miUnlockDate(mi) === plusD(53), miUnlockDate(mi));
  ck('间隔改成 60 → 卡片上的天数跟着变（还有 53 天）',
     /还有 53 天/.test(metaOf('医保甲片')), metaOf('医保甲片'));

  // 提前量 0 = 到期当天才解锁
  settings.miEarly = 0; render();
  ck('提前 0 天 → 解锁日 = 开药日 + 60', miUnlockDate(mi) === plusD(60), miUnlockDate(mi));
  // 提前量大过间隔 → 夹到间隔（否则一开完就解，锁等于不存在）
  settings.miDays = 10; settings.miEarly = 99; render();
  ck('提前量填得比间隔还大 → 夹到间隔（解锁日 = 开药日 + 0）',
     miUnlockDate(mi) === plusD(0), miUnlockDate(mi));
  ck('这时候今天开今天就解锁（锁确实等于不存在，所以保存时会被拦）',
     miLocked(mk({ name: '极短锁片', ins: 'mi', miStart: todayStr() })) === false, '');

  /* ================= 2. 到日子自己解开（不存"锁没锁"） ================= */
  reset();
  var gone = mk({ name: '早开的片', ins: 'mi', miStart: agoD(24), stock: 30 });   // 24 天前开
  ck('24 天前开的（间隔 30、提前 7 → 第 23 天解）→ 已经解锁', miLocked(gone) === false,
     miUnlockDate(gone));
  ck('解锁后卡片上写「已经可以再开」', /已经可以再开/.test(metaOf('早开的片')), metaOf('早开的片'));
  ck('解锁后药名旁的小标不再是 🔒', cardOf('早开的片').querySelector('.ins-lock') === null,
     cardOf('早开的片').querySelector('.ins-open') ? '医保(绿)' : '(无)');
  ck('★ 解锁后仍然记着「这味是医保开的」（小标还在，只是不锁）',
     cardOf('早开的片').querySelector('.ins-open') !== null, '');

  var edge = mk({ name: '卡点片', ins: 'mi', miStart: agoD(23), stock: 30 });     // 正好第 23 天
  ck('★ 解锁日当天就解开（不是「过了才解」）', miLocked(edge) === false, miUnlockDate(edge));
  edge.miStart = agoD(22); save(); render();
  ck('早一天（第 22 天）→ 还锁着，还剩 1 天', miLocked(edge) === true
     && miDaysToUnlock(miUnlockDate(edge)) === 1, miUnlockDate(edge));

  /* ================= 3. 卡片上的锁标志 ================= */
  reset();
  mk({ name: '锁着的片', ins: 'mi', miStart: todayStr(), stock: 3 });
  ck('药名旁有「🔒 医保」小标', cardOf('锁着的片').querySelector('.med-name .ins-lock') !== null,
     cardOf('锁着的片').querySelector('.med-name').textContent.replace(/\s+/g, ' ').trim());
  ck('小标文字里有「医保」和锁', /医保/.test(cardOf('锁着的片').querySelector('.ins-lock').textContent),
     cardOf('锁着的片').querySelector('.ins-lock').textContent);
  ck('meta 行写「医保锁到 X月X日（还有 N 天）」', /医保锁到/.test(metaOf('锁着的片'))
     && /还有 23 天/.test(metaOf('锁着的片')), metaOf('锁着的片'));

  /* ================= 4. 添加药品：自费 / 医保 ================= */
  reset();
  openEditor(null);
  ck('新建药品默认选「自费买的」', q('#formMed').ins.value === 'self', q('#formMed').ins.value);
  ck('选自费时医保日期那一格是收起来的', q('#miWrap').style.display === 'none',
     q('#miWrap').style.display);
  q('#dlgMed').close();

  await addViaForm({ name: '自费乙片', ins: 'self', stock: 30 });
  var selfM = byName('自费乙片');
  ck('自费保存后 ins 是空的（没有医保标记）', selfM.ins === '', String(selfM.ins));
  ck('自费保存后没有 miStart', !selfM.miStart, String(selfM.miStart));
  ck('自费那味不上锁', miLocked(selfM) === false, '');
  ck('自费那味卡片上没有医保小标', cardOf('自费乙片').querySelector('.tag.ins-lock, .tag.ins-open') === null, '');

  await addViaForm({ name: '医保丙片', ins: 'mi', stock: 30 });
  var miM = byName('医保丙片');
  ck('医保保存后 ins = mi', miM.ins === 'mi', miM.ins);
  ck('医保保存后 miStart = 今天（表单默认今天）', miM.miStart === todayStr(), miM.miStart);
  ck('医保那味立刻锁上', miLocked(miM) === true, '');
  ck('医保那味卡片有 🔒 小标', cardOf('医保丙片').querySelector('.ins-lock') !== null, '');

  await addViaForm({ name: '补录上月的片', ins: 'mi', miStart: agoD(40), stock: 30 });
  var back = byName('补录上月的片');
  ck('补录一个 40 天前的开药日 → 解锁日是 40−23 天前（早过了）',
     miUnlockDate(back) === plusD(-40 + 23), miUnlockDate(back));
  ck('补录的这味不锁（日子早过了）', miLocked(back) === false, '');

  // 编辑：原本医保的药改成自费 → 锁必须消失
  openEditor(miM.id);
  ck('编辑已医保的药：来源回填成「医保开的」', q('#formMed').ins.value === 'mi',
     q('#formMed').ins.value);
  ck('编辑时医保日期回填成原来的开药日', q('#formMed').miStart.value === todayStr(),
     q('#formMed').miStart.value);
  ck('编辑时展开着医保日期那一格', q('#miWrap').style.display !== 'none', q('#miWrap').style.display);
  q('#formMed').ins.value = 'self';
  await submitMed({ preventDefault: function(){} });
  var after = byName('医保丙片');
  ck('改成自费保存后 ins 清空', after.ins === '', String(after.ins));
  ck('改成自费保存后 miStart 清空', !after.miStart, String(after.miStart));
  ck('改成自费后锁就没了', miLocked(after) === false, '');

  /* ================= 5. 又买了药：要问自费还是医保 ================= */
  reset();
  var rs = mk({ name: '补货片', stock: 10 });
  var got = await restock(rs.id, 7, 'self');
  ck('「又买了药」弹窗里有来源选择（不是所有加药都默认医保）', got.shown === true, String(got.shown));
  ck('选自费时小字说明「不动医保锁」', /不动医保锁/.test(got.tip), got.tip);
  ck('自费补货：数量加上了（10 + 7）', stockNow(byName('补货片')) === 17, stockNow(byName('补货片')));
  ck('自费补货：没有被打上医保标记', !byName('补货片').ins, String(byName('补货片').ins));
  ck('自费补货：不上锁', miLocked(byName('补货片')) === false, '');

  var got2 = await restock(rs.id, 5, 'mi');
  ck('选医保时小字写清会锁到哪天', /锁到/.test(got2.tip) || /之后才能/.test(got2.tip), got2.tip);
  var rs2 = byName('补货片');
  ck('医保补货：ins = mi', rs2.ins === 'mi', rs2.ins);
  ck('医保补货：miStart 滚到今天', rs2.miStart === todayStr(), rs2.miStart);
  ck('医保补货：锁上了', miLocked(rs2) === true, '');
  ck('医保补货：数量照样加上（17 + 5）', stockNow(rs2) === 22, stockNow(rs2));

  // 锁着的时候自费再买一次 —— 锁不许被解开（自费补货不是"又开了一次药"）
  await restock(rs2.id, 3, 'self');
  ck('★ 锁着时自费补货，原来的锁还在（不会因为买了药就解锁）',
     miLocked(byName('补货片')) === true, miUnlockDate(byName('补货片')));
  ck('自费补货后 ins 还是 mi（这味本来就算医保开的）', byName('补货片').ins === 'mi',
     byName('补货片').ins);

  /* ================= 6. 校准余量：不碰锁 ================= */
  reset();
  var cal = mk({ name: '校准片', ins: 'mi', miStart: todayStr(), stock: 20 });
  var beforeStart = cal.miStart;
  calibMed(cal.id);
  await null;
  ck('「校准余量」弹窗里不出现来源选择',
     !q('#numIns') || q('#numIns').hidden === true, q('#numIns') ? String(q('#numIns').hidden) : '无');
  q('#numVal').value = '9';
  await submitNum({ preventDefault: function(){} });
  ck('校准后数量变成 9', stockNow(byName('校准片')) === 9, stockNow(byName('校准片')));
  ck('★ 校准余量不动医保锁（开药日原样）', byName('校准片').miStart === beforeStart,
     byName('校准片').miStart);
  ck('校准后仍然锁着', miLocked(byName('校准片')) === true, '');

  /* 注：「批量调存量」那一页 2026-11-19 按用户要求删了（改成卡片勾选 + 已经开药），
     原来这里那条「批量调存量不动锁」的断言随之失效。同一件事（不是一次开药的操作不碰锁）
     已由上面的校准余量钉住；勾选制本身在下面第 7 节接着验。 */

  /* ================= 7. 已经开药：默认按医保记，且只动勾上的 ================= */
  reset();
  mk({ name: '要开的片', stock: 2 });                       // 2 天 → 偏少，进"要开的药"
  pickSet(byName('要开的片').id, true);                      // 2026-11-19 起要先勾
  render();
  await openDisp();
  ck('「已经开药」默认选「医保开的」',
     q('#formDisp input[name=dispIns]:checked').value === 'mi',
     q('#formDisp input[name=dispIns]:checked').value);
  ck('小字说清这批会一起上锁', /都会上锁/.test(q('#dispInsTip').textContent), q('#dispInsTip').textContent);
  await submitDisp({ preventDefault: function(){} });
  var dispM = byName('要开的片');
  ck('已经开药（医保）→ ins = mi', dispM.ins === 'mi', String(dispM.ins));
  ck('已经开药（医保）→ miStart = 今天', dispM.miStart === todayStr(), dispM.miStart);
  ck('已经开药（医保）→ 锁上了', miLocked(dispM) === true, '');

  // 换成自费再开一次
  reset();
  mk({ name: '自费开的片', stock: 2 });
  pickSet(byName('自费开的片').id, true);
  render();
  await openDisp();
  q('#formDisp input[name=dispIns][value=self]').checked = true;
  q('#formDisp').dispatchEvent(new Event('change', { bubbles: true }));
  ck('切到自费时小字改成「不上锁」', /不上锁/.test(q('#dispInsTip').textContent), q('#dispInsTip').textContent);
  await submitDisp({ preventDefault: function(){} });
  ck('已经开药（自费）→ 不打医保标记', !byName('自费开的片').ins, String(byName('自费开的片').ins));
  ck('已经开药（自费）→ 不上锁', miLocked(byName('自费开的片')) === false, '');

  // ★ 勾选制：只勾一味，另一味（哪怕也该开）一根手指都不许碰
  reset();
  mk({ name: '勾上的片', stock: 2 });
  mk({ name: '没勾的片', stock: 1 });
  pickSet(byName('勾上的片').id, true);
  render();
  ck('没勾的那味不进入库计划', dispItems().length === 1 && dispItems()[0].name === '勾上的片',
     dispItems().map(function(m){ return m.name; }).join('/'));
  await openDisp();
  await submitDisp({ preventDefault: function(){} });
  ck('勾上的那味上了锁', miLocked(byName('勾上的片')) === true, '');
  ck('★ 没勾的那味：没入库、也没上锁',
     stockNow(byName('没勾的片')) === 1 && !byName('没勾的片').ins
       && miLocked(byName('没勾的片')) === false,
     stockNow(byName('没勾的片')) + ' / ' + String(byName('没勾的片').ins));
  ck('入库完勾选清空', pickCount() === 0, pickCount());

  /* ================= 8. 底方录入：总开关 + 逐行改 ================= */
  reset();
  await openRx([
    { name: '底方甲片', spec: '5mg', perDose: 1, unit: '片', cycleDays: 1, timesPerCycle: 1, total: 30 },
    { name: '底方乙片', spec: '5mg', perDose: 1, unit: '片', cycleDays: 1, timesPerCycle: 1, total: 30 }
  ], '', null, null);
  ck('底方录入默认按「医保开的」', q('#rxInsBar input[name=rxIns]:checked').value === 'mi',
     q('#rxInsBar input[name=rxIns]:checked').value);
  ck('★ 底方录入的行带着 ins 字段（默认 mi）',
     rxRows.length === 2 && rxRows[0].ins === 'mi', rxRows.length ? rxRows[0].ins : '无行');
  ck('每行药名后面有一个「医保/自费」小标', qa('#rxList .rx-insb').length === 2,
     qa('#rxList .rx-insb').length);

  // 单独翻第二行 → 只有它变自费，第一行不动
  qa('#rxList .rx-insb')[1].click();
  ck('点第二行的小标 → 这一行变自费', rxRows[1].ins === '', String(rxRows[1].ins));
  ck('点第二行的小标 → 第一行还是医保（只翻一行）', rxRows[0].ins === 'mi', String(rxRows[0].ins));
  ck('小标文字跟着变成「自费」', qa('#rxList .rx-insb')[1].textContent === '自费',
     qa('#rxList .rx-insb')[1].textContent);

  await submitRx({ preventDefault: function(){} });
  var rx1 = byName('底方甲片'), rx2 = byName('底方乙片');
  ck('底方入库：医保那味 ins = mi', rx1.ins === 'mi', String(rx1.ins));
  ck('底方入库：医保那味 miStart = 今天', rx1.miStart === todayStr(), rx1.miStart);
  ck('底方入库：医保那味锁上了', miLocked(rx1) === true, '');
  ck('底方入库：单独改成自费那味没有标记', !rx2.ins, String(rx2.ins));
  ck('底方入库：自费那味不上锁', miLocked(rx2) === false, '');
  ck('入库提示里说清医保那几味锁到哪天',
     R.alerts.some(function(a){ return /按「医保开的」记/.test(a); }),
     (R.alerts[R.alerts.length - 1] || '').slice(0, 60));

  // 总开关一改，所有行一起翻
  reset();
  await openRx([
    { name: '底方丙片', spec: '5mg', perDose: 1, unit: '片', cycleDays: 1, timesPerCycle: 1, total: 30 },
    { name: '底方丁片', spec: '5mg', perDose: 1, unit: '片', cycleDays: 1, timesPerCycle: 1, total: 30 }
  ], '', null, null);
  var sw = q('#rxInsBar input[name=rxIns][value=self]');
  sw.checked = true;
  sw.dispatchEvent(new Event('change', { bubbles: true }));
  ck('总开关切到自费 → 所有行都变成自费',
     rxRows.every(function(r){ return r.ins === ''; }),
     rxRows.map(function(r){ return r.ins || '自费'; }).join('/'));
  ck('总开关切到自费 → 小字改成"点小标翻过来"', /小标/.test(q('#rxInsTip').textContent),
     q('#rxInsTip').textContent);
  await submitRx({ preventDefault: function(){} });
  ck('全自费的一批 → 两味都没有医保标记',
     !byName('底方丙片').ins && !byName('底方丁片').ins,
     String(byName('底方丙片').ins) + '/' + String(byName('底方丁片').ins));

  // 关掉再打开要回到默认的医保
  reset();
  await openRx([{ name: '底方戊片', spec: '5mg', perDose: 1, unit: '片',
                  cycleDays: 1, timesPerCycle: 1, total: 30 }], '', null, null);
  var sw2 = q('#rxInsBar input[name=rxIns][value=self]');
  sw2.checked = true;
  sw2.dispatchEvent(new Event('change', { bubbles: true }));
  q('#dlgRx').close();
  await openRx([{ name: '底方己片', spec: '5mg', perDose: 1, unit: '片',
                  cycleDays: 1, timesPerCycle: 1, total: 30 }], '', null, null);
  ck('★ 关掉再打开，来源回到默认的「医保开的」',
     q('#rxInsBar input[name=rxIns]:checked').value === 'mi',
     q('#rxInsBar input[name=rxIns]:checked').value);
  await submitRx({ preventDefault: function(){} });

  /* ================= 9. 就诊准备 / 全家偏少 里的提醒 ================= */
  reset();
  mk({ name: '该开且锁着片', ins: 'mi', miStart: todayStr(), stock: 3 });
  mk({ name: '该开没锁片', stock: 3 });
  render();
  /* ★ 2026-11-19 用户删掉了清单下面那条「另外 N 味已经该开了，但还在医保锁定期」——
     原话：「要开的药下边那个提示啊是不成立的，它并不是那个该开了，因为这个存量还很很多呢」。
     判断本身就不牢靠（没录上限的药只能按提醒天数猜），所以整块拆掉，连节点都不留。
     这条断言就是来拦"某个版本又把它加回去"的。 */
  ck('★ 清单下面不再有「另外 N 味已经该开了」那条提示（节点已删）',
     q('#insTip') === null, q('#insTip') ? '还在' : '已删除');
  // ★ 用 innerText 而不是 textContent：textContent 会把 <script> 里的源码也算进来，
  //   那句「不写『已经该开了』」的注释会自己把自己判成失败。
  ck('  页面正文里也不再出现"已经该开了"这个说法',
     q('body').innerText.indexOf('已经该开了') < 0, '');
  ck('  锁着 + 缺的那味仍然不进清单（口径没跟着一起松掉）',
     refillItems().map(function(m){ return m.name; }).indexOf('该开且锁着片') < 0,
     refillItems().map(function(m){ return m.name; }).join(' / '));

  reset();
  mk({ name: '全家锁片', ins: 'mi', miStart: todayStr(), stock: 2 });
  openFam();
  ck('全家偏少那一行也带 🔒 和「医保锁到」',
     /医保锁到/.test((q('#famList') || {}).textContent || ''),
     ((q('#famList') || {}).textContent || '').replace(/\s+/g, ' ').slice(0, 60));
  ck('★ 复制出去的文字里也带「医保锁到」（照着买的人才不会白跑）',
     /医保锁到/.test(famCopyText()), famCopyText());

  /* ================= 10. 设置 ================= */
  reset();
  openSettings();
  ck('设置里能填「医保开药间隔」并回填当前值', String(q('#formSet').miDays.value) === '30',
     q('#formSet').miDays.value);
  ck('设置里能填「提前几天解锁」并回填当前值', String(q('#formSet').miEarly.value) === '7',
     q('#formSet').miEarly.value);
  q('#formSet').miDays.value = '45';
  q('#formSet').miEarly.value = '10';
  await submitSettings({ preventDefault: function(){} });
  ck('保存后 settings.miDays = 45', settings.miDays === 45, settings.miDays);
  ck('保存后 settings.miEarly = 10', settings.miEarly === 10, settings.miEarly);
  ck('新参数立刻生效：今天开的锁到 今天+35',
     miUnlockFrom(todayStr()) === plusD(35), miUnlockFrom(todayStr()));

  q('#formSet').miDays.value = '10';
  q('#formSet').miEarly.value = '99';
  R.alerts = [];
  await submitSettings({ preventDefault: function(){} });
  ck('提前量比间隔还大 → 被夹到间隔', settings.miEarly === 10, settings.miEarly);
  ck('提前量比间隔还大 → 明确告诉用户夹成多少（不静默改数）',
     R.alerts.some(function(a){ return /提前解锁填得太多了/.test(a); }),
     R.alerts.join(' | ').slice(0, 80));
  ck('设置里那句提示写明了只影响医保的药',
     /自费买的药不上锁/.test(q('#dlgSet').textContent.replace(/\s+/g, '')),
     '');
  q('#dlgSet').close();

  /* ================= 11. 备份往返：字段不许在路上丢 ================= */
  reset();
  mk({ name: '备份锁片', ins: 'mi', miStart: todayStr(), stock: 10 });
  mk({ name: '备份自费片', stock: 10 });
  var snap = JSON.parse(JSON.stringify({ v: 2, currentId: currentId, people: people }));
  var back2 = migrate(snap);
  var backMeds = back2.people[0].meds;
  var b1 = backMeds.filter(function(m){ return m.name === '备份锁片'; })[0];
  var b2 = backMeds.filter(function(m){ return m.name === '备份自费片'; })[0];
  ck('整包备份往返：医保标记还在', b1 && b1.ins === 'mi', b1 ? b1.ins : '无');
  ck('整包备份往返：开药日还在（锁没被路上解开）', b1 && b1.miStart === todayStr(),
     b1 ? b1.miStart : '无');
  ck('整包备份往返：自费那味仍然没有标记', b2 && !b2.ins, b2 ? String(b2.ins) : '无');

  /* ================= 12. 清单里每天吃的药分「医保 / 自购」两段（2026-11-19） ======
     用户原话：「每天吃的药要区分出是医保还是自购。在没有标明是医保的时候，就把这个药品
               放到自购里。但是如果标明了是医保就放回医保。」
     钉四件事：
       ① 每天吃的药下面真有两段，医保在前、自购在后（医保带锁，是要先看的那类）；
       ② 没标 ins 的（老存档 / 新加的默认）都在「自购」段 —— 不做任何"补录"；
       ③ 标了 mi 的进「医保」段 —— 用的还是同一个 ins，不另存"分类"字段；
       ④ 把一味改成医保（再去掉标记），它**当场换段**，条数跟着变。
     ★ 段标题是 .sub-h，**不是** .sect-h —— test_prn 里数的是 .sect-h 的个数
       （每天吃的 / 备用药那两个），这里再塞一个 .sect-h 会把那条断言顶掉。
     ★ segOf 遇到 .sect-h 就停：备用药卡片前面最近的一段是一级标题，
       不是"自购"段 —— 不拦的话它会一路往上找到自购段，误判成"备用药也是自购"。 */
  reset();
  var dSelf = mk({ name: '自购甲片', stock: 10 });
  var dMi   = mk({ name: '医保乙片', ins: 'mi', miStart: todayStr(), stock: 10 });
  var dOld  = mk({ name: '老存档丙片', stock: 10 });          // 完全没有 ins / miStart
  mk({ name: '备用丁片', kind: 'prn', stock: 3 });

  function subs(){ return qa('#list .sub-h'); }
  function subText(i){
    return (subs()[i] ? subs()[i].textContent : '').replace(/\s+/g, ' ').trim();
  }
  function segOf(n){
    var c = cardOf(n);
    if(!c) return '(无卡片)';
    var prev = c.previousElementSibling;
    while(prev){
      if(prev.classList.contains('sect-h')) return '(一级分组：' + prev.textContent.replace(/\s+/g, ' ').trim() + ')';
      if(prev.classList.contains('sub-h')) return prev.textContent.replace(/\s+/g, ' ').trim();
      prev = prev.previousElementSibling;
    }
    return '(没有段标题)';
  }

  ck('每天吃的药下面分成两段', subs().length === 2,
     subs().map(function(el){ return el.textContent.replace(/\s+/g, ' ').trim(); }).join(' | '));
  ck('第一段是医保、第二段是自购（医保在前）',
     /^医保/.test(subText(0)) && /^自购/.test(subText(1)), subText(0) + ' / ' + subText(1));
  ck('两段都带条数（医保 1 种 / 自购 2 种）',
     /1\s*种/.test(subText(0)) && /2\s*种/.test(subText(1)), subText(0) + ' / ' + subText(1));
  ck('★ 没标过 ins 的都落在「自购」里（新加的默认 + 老存档）',
     /^自购/.test(segOf('自购甲片')) && /^自购/.test(segOf('老存档丙片')),
     segOf('自购甲片') + ' | ' + segOf('老存档丙片'));
  ck('★ 标了医保的落在「医保」里', /^医保/.test(segOf('医保乙片')), segOf('医保乙片'));
  ck('备用药不进这两段（它归一级标题「备用药」）',
     /一级分组/.test(segOf('备用丁片')), segOf('备用丁片'));
  ck('提示语里「每天吃的 3 种」和「医保 1 / 自购 2」对得上',
     /每天吃的\s*3\s*种/.test(q('#listHint').textContent)
       && /医保\s*1\s*\/\s*自购\s*2/.test(q('#listHint').textContent),
     q('#listHint').textContent);

  // ---- 改标记 → 当场换段 ----
  dSelf.ins = 'mi'; dSelf.miStart = todayStr(); save(); render();
  ck('★ 把一味从自购改成医保，它当场换到医保段',
     /^医保/.test(segOf('自购甲片')), segOf('自购甲片'));
  ck('  两段条数跟着变（医保 2 种 / 自购 1 种）',
     /2\s*种/.test(subText(0)) && /1\s*种/.test(subText(1)), subText(0) + ' / ' + subText(1));
  ck('  改过来的这味照样挂上医保锁标（不是只换了位置）',
     !!cardOf('自购甲片').querySelector('.tag.ins-lock, .tag.ins-open'), '');

  dSelf.ins = ''; delete dSelf.miStart; save(); render();
  ck('★ 去掉医保标记 → 回到自购段（分类就是 ins 本身，没有第二个字段）',
     /^自购/.test(segOf('自购甲片')), segOf('自购甲片'));
  ck('  回去之后医保段只剩 1 种', /1\s*种/.test(subText(0)), subText(0));

  // 全是自购时：两段只剩一段也得显示 —— 否则"没标明 = 自购"这条规则在界面上没痕迹
  setMeds([dOld]); save(); render();
  ck('★ 只有自购的药时，那一段仍然写出来（不然看不出它们被归到哪儿了）',
     subs().length === 1 && /^自购/.test(subText(0)), subText(0));
  ck('  这一段的条数是 1 种', /1\s*种/.test(subText(0)), subText(0));
  reset();

  /* ========= 12.5 清单只收「锁已开 + 数量确实不够」的（2026-11-19）=========
     用户原话：「这个要开的药的这个清单啊，一定是在这个医保锁已经解锁之后，然后呢这个
               药品的这个剩余的数量小于这个一次能开的这个药量之后，才会显示在这个清单里。」
     两个条件都得满足，缺一不进；反过来说：
       ① 医保锁着（离解锁日还早）、但确实快吃完了 → **不进**清单；
       ② 同样的药，只把开药日往前挪到解锁之后 → **自动进**清单（没有任何"手动加回"的动作，
          因为锁是算出来的）；
       ③ 自费药没有锁，行为一点不变（够该开就进）；
       ④ 缺的都锁着时，空文案要说清"现在开不出来"，不能写成"药还够"。
     ★ 清单下面那条「另外 N 味已经该开了、但还在医保锁定期」2026-11-19 已被用户删掉：
       那个"该开了"是程序瞎猜的（没录上限的药只能按提醒天数兜底，存量很多也会被判成该开），
       留着就是错话。所以这里只验"清单收谁不收谁"，不再验那条提示 —— 也别再把它加回来。 */
  reset();
  var lockShort = mk({ name: '锁着且缺片', ins: 'mi', miStart: todayStr(), stock: 3 });
  var selfShort = mk({ name: '自费且缺片', stock: 3 });
  var poolOk    = mk({ name: '锁着但不缺片', ins: 'mi', miStart: todayStr(), stock: 200 });
  render();

  ck('★ 医保锁着、只剩 3 天的药不进「可以开的药」清单',
     refillItems().map(function(m){ return m.name; }).indexOf('锁着且缺片') < 0,
     refillItems().map(function(m){ return m.name; }).join(' / ') || '(空)');
  ck('  清单里只有自费那味（2 味缺、只收 1 味）',
     refillItems().length === 1 && refillItems()[0].name === '自费且缺片',
     refillItems().map(function(m){ return m.name; }).join(' / '));
  ck('★ 复制/下载那段文字里也没有它（不然粘进问诊框还是白写）',
     buildRefillText().indexOf('锁着且缺片') < 0 && buildRefillText().indexOf('自费且缺片') >= 0,
     buildRefillText().replace(/\n/g, ' / '));
  ck('  标题右边那个数也跟着只数 1 种', q('#drugTip').textContent === '1 种',
     q('#drugTip').textContent);
  ck('★ 那条 🔒 提示已经不在了（判断不成立，整块拆掉）',
     q('#insTip') === null, q('#insTip') ? '还在' : '已删除');
  ck('  锁着但不缺的那味清单里没有、页面正文里也不点名',
     buildRefillText().indexOf('锁着但不缺片') < 0
       && q('#refillText').textContent.indexOf('锁着但不缺片') < 0, '');

  // ---- ② 只把开药日往前挪：锁自己开，清单自己收 ----
  lockShort.miStart = agoD(30);           // 30 天前开的 → 解锁日已过
  save(); render();
  ck('★ 开药日挪到 30 天前 → 锁自己开了，这味自动进清单（没有"手动加回"这一步）',
     !miLocked(byName('锁着且缺片'))
       && refillItems().length === 2
       && refillItems().map(function(m){ return m.name; }).indexOf('锁着且缺片') >= 0,
     'locked=' + miLocked(byName('锁着且缺片'))
       + ' / ' + refillItems().map(function(m){ return m.name; }).join(' / '));

  // ---- ③ 自费药不受影响：锁对它恒为 false ----
  ck('★ 自费药一直在清单里（它没有锁，规则只作用于医保）',
     !miLocked(byName('自费且缺片'))
       && refillItems().map(function(m){ return m.name; }).indexOf('自费且缺片') >= 0, '');

  // ---- ④ 缺的都锁着：空文案得说清"现在开不出来"，不是"药还够" ----
  reset();
  mk({ name: '全锁着片', ins: 'mi', miStart: todayStr(), stock: 2 });
  render();
  ck('全都锁着时清单是空的', refillItems().length === 0, refillItems().length);
  ck('★ 但空文案说的是「暂时没有可以开的药」，并点明是医保锁的事',
     /暂时没有可以开的药/.test(q('#refillText').textContent)
       && /医保锁定期/.test(q('#refillText').textContent),
     q('#refillText').textContent.replace(/\s+/g, ' '));
  ck('  不说「该开了」（那句判断程序做不了）',
     q('#refillText').textContent.indexOf('该开了') < 0,
     q('#refillText').textContent.replace(/\s+/g, ' '));

  // ---- 全都不缺时，仍然是那句"都还够吃" ----
  reset();
  mk({ name: '够吃片', stock: 200 });
  render();
  ck('药都够吃时换一句说「都还够」',
     /目前没有可以开的药/.test(q('#refillText').textContent)
       && /够「一次最多能开几天」/.test(q('#refillText').textContent),
     q('#refillText').textContent.replace(/\s+/g, ' '));
  reset();

  /* ========= 12.6 人名后面那个数字 = 「剩不到 7 天」的味数（2026-11-19）=======
     用户原话：「最上边的那个人啊旁边有一个数字显示的是有哪几种药少，然后呢这个是和这个
               药品存量少于这个 7 天来去计算的。」
     它跟清单**不是一个口径**，这一点最容易在以后被"顺手统一"掉，所以逐条钉住：
       ① 剩 5 天 → 数；剩 6.9 天 → 数（"少于 7 天"确实不含 7）；
       ② 正好 7 天 → 不数；剩 10 天但上限 30 天（清单里有它）→ **也不数** ← 这次改动的核心；
       ③ 已经用完（0 天）→ 数；备用药（不算天数）→ 不数；
       ④ 医保锁着但只剩 5 天 → 照数（角标只看存量，不管开不开得出来）；
       ⑤ 数字只算当前这个人，切人跟着换；不挂 title 的 0 味不画角标。
     ★ 别把 lowDays 换成 shortNow/inRefill —— 那等于把口径退回 30 天，这条需求就没了。 */
  reset();
  mk({ name: '剩五天片', stock: 5 });                       // 5 天 → 数
  mk({ name: '剩十天片', stock: 10 });                      // 10 天（上限 30 → 清单里有）→ 不数
  mk({ name: '正好七天片', stock: 7 });                     // 边界 7 → 不数
  mk({ name: '剩六点九天片', times: 10, stock: 69 });       // 6.9 天 → 数
  mk({ name: '空了片', stock: 0 });                         // 0 天 → 数
  mk({ name: '备用片', kind: 'prn', stock: 3 });            // 不算天数 → 不数
  mk({ name: '锁着五天片', ins: 'mi', miStart: todayStr(), stock: 5 });   // 锁着但快没了 → 数
  render();

  function myBadge(){ return q('#people .ptab.active .badge'); }
  function badgeTxt(){ return myBadge() ? myBadge().textContent.trim() : '(无角标)'; }

  ck('★ 角标数的是「剩不到 7 天」的味数（5 / 6.9 / 0 / 锁着那味 = 4）',
     !!myBadge() && myBadge().textContent.trim() === '4', badgeTxt());
  ck('★ 剩 10 天、上限 30 天的药不算进去（它在清单里，但还没到"要没了"）',
     lowDays(byName('剩十天片')) === false && shortNow(byName('剩十天片')) === true
       && refillItems().map(function(m){ return m.name; }).indexOf('剩十天片') >= 0,
     'low=' + lowDays(byName('剩十天片')) + ' short=' + shortNow(byName('剩十天片')));
  ck('正好 7 天不数（"少于 7 天"不含 7）', lowDays(byName('正好七天片')) === false,
     daysLeft(byName('正好七天片')));
  ck('6.9 天要数（差一点点也不行）', lowDays(byName('剩六点九天片')) === true,
     daysLeft(byName('剩六点九天片')));
  ck('已经用完的（0 天）当然算', lowDays(byName('空了片')) === true,
     daysLeft(byName('空了片')));
  ck('备用药不数（它没有"还剩几天"这回事）', lowDays(byName('备用片')) === false,
     String(daysLeft(byName('备用片'))));
  ck('★ 医保锁着但只剩 5 天的照数（角标看存量，不看开不开得出来）',
     lowDays(byName('锁着五天片')) === true && miLocked(byName('锁着五天片')),
     'low=' + lowDays(byName('锁着五天片')));
  ck('角标上挂了说明（鼠标停上去能知道这个数是什么）',
     /剩不到\s*7\s*天/.test(myBadge().getAttribute('title') || ''),
     myBadge().getAttribute('title'));
  ck('人名那个 title 也把它说清了', /4 味剩不到 7 天/.test(q('#people .ptab.active').getAttribute('title') || ''),
     q('#people .ptab.active').getAttribute('title'));

  // 切人：数字只算这个人自己的药
  var mom6 = { id: uid(), name: '妈妈', note: '', box: { slots: defaultSlots(3) }, meds: [] };
  (function(){
    var mm = { id: uid(), kind: 'daily', name: '妈妈只剩两天片', spec: '5mg', perDose: 1, unit: '片',
               cycleDays: 1, timesPerCycle: 1, stockBase: 2, stockDate: todayStr(),
               rxMax: 30, rxMaxDay: 1, note: '' };
    mom6.meds.push(mm);
  })();
  people.push(mom6); save(); render();
  var momTab = q('#people .ptab[data-pid="' + mom6.id + '"] .badge');
  ck('★ 别人名下只挂 1（各自算各自的，不串）', !!momTab && momTab.textContent.trim() === '1',
     momTab ? momTab.textContent.trim() : '(无角标)');
  ck('  当前这个人还是 4', myBadge() && myBadge().textContent.trim() === '4', badgeTxt());

  // 没有一味快没了 → 不画角标（0 不出数字，免得多一个"0"吓人）
  reset();
  mk({ name: '很够吃片', stock: 60 });
  render();
  ck('一味都不少于 7 天时不画角标', !myBadge(), badgeTxt());
  reset();

  /* ========= 12.7 星号：没填「一次最多能开几天」的药，程序判不了 =========
     用户 2026-11-19 原话：「在这个药品旁边加一个星，然后呢下边的那个是对这个星的解释，就是说
               因为没有录入一次可以开多少药，所以程序无法判断。而且这个不是要开的药，而是可以开的药。」
     所以这一节钉四件事：
       ① 区块标题叫「可以开的药」（不是「要开的药」）；
       ② 没填上限的药名后面挂 *，填了的不挂（星号只跟"有没有尺子"走，跟缺不缺没关系）；
       ③ 星号下面有一行解释，说清是"没录上限所以判不了"，并且只在真有星时才出现；
       ④ 复制/下载的文本里**没有**星号 —— 那是给人看的记号，医生看不懂。 */
  reset();
  mk({ name: '没填上限片', stock: 3, rxMax: 0 });     // 没尺子 → 判不了 → 带星
  mk({ name: '填了上限片', stock: 3 });               // 有尺子 → 不带星
  render();
  var rHtml = q('#refillText').innerHTML;
  var sBox = q('#refillStar');
  ck('★ 区块标题叫「可以开的药」', q('#refillText').parentNode.querySelector('.blk-t').textContent === '可以开的药',
     q('#refillText').parentNode.querySelector('.blk-t').textContent);
  ck('★ 没填上限的那味药名后面挂了星', /没填上限片<i class="rstar"/.test(rHtml), rHtml.replace(/\n/g, ' / '));
  ck('  填了上限的那味不带星', rHtml.indexOf('填了上限片<i') < 0, rHtml.replace(/\n/g, ' / '));
  ck('★ 星下面有解释，说清"没录上限 → 判不了该不该开"',
     sBox.hidden === false && /没填「一次最多能开几天」/.test(sBox.textContent)
       && /没法判断/.test(sBox.textContent),
     sBox.textContent.replace(/\s+/g, ' '));
  ck('  解释里点了星的数量（1 味）', /1\s*味/.test(sBox.textContent),
     sBox.textContent.replace(/\s+/g, ' '));
  ck('★ 复制/下载的文字里没有星号（那是界面上的记号）',
     buildRefillText().indexOf('*') < 0
       && buildRefillText().indexOf('没填上限片') >= 0, buildRefillText().replace(/\n/g, ' / '));

  // 全都有上限 → 一个星也没有，那行解释跟着收起来
  reset();
  mk({ name: '都有上限甲片', stock: 3 });
  mk({ name: '都有上限乙片', stock: 2 });
  render();
  ck('★ 全都有上限时，清单里一个星都不挂', q('#refillText').innerHTML.indexOf('rstar') < 0,
     q('#refillText').innerHTML.replace(/\n/g, ' / '));
  ck('  那行解释也跟着收起来（不留一条空壳）', q('#refillStar').hidden === true,
     String(q('#refillStar').hidden));
  reset();

  /* ================= 13. 全程不许走原生弹窗 ================= */
  ck('★ 整个过程一次都没走浏览器原生 alert / confirm / prompt',
     R.natives.length === 0, R.natives.join(' | ') || '(一次都没有)');

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
    tmpdir = tempfile.mkdtemp(prefix="dk_ins_")
    page = os.path.join(tmpdir, "t.html")
    open(page, "w", encoding="utf-8").write(src[:i] + PROBE + src[i:])

    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--virtual-time-budget=8000",
           "--user-data-dir=" + os.path.join(tmpdir, "prof"), "--dump-dom", url]
    r = subprocess.run(cmd, capture_output=True, timeout=240)
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
