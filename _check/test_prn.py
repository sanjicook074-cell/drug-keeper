# -*- coding: utf-8 -*-
"""
drug-keeper：两类药（每天都在吃 / 备用药）—— 真实 DOM 驱动测试。

用户 2026-10-22 的要求，原话：
    「药品应该分为每天都在吃的药和备用药，备用药不用跟着天数走」

这条要求拆成两半，本脚本就是按这两半钉的：
  ① **分得开**：列表分成两栏（每天吃的 / 备用药）、统计栏分开数、表单里能选类型；
  ② **备用药不跟天数走**：不按用法扣、不算「剩几天」、不进续方清单 / 已经开药 /
     批量调存量，余量只由「又买了药 / 校准余量」改。

★ 口径**不是新写一套**，而是让 `dailyUse()` 对备用药返回 0：
  它是「按天推」的总阀门，一旦为 0，daysLeft / needUnits / inRefill / dispItems /
  bulkRows 全都自动不参与。所以下面的断言一大半是在验「下游确实被这一个 0 关掉了」，
  而不是在验每个函数各写了一遍 if（那种写法迟早会漏一处）。

做法同 test_rxcap.py：把 index.html 复制成临时副本，在主 IIFE 内部末尾注入探针，
用无头 Chrome --dump-dom 取回渲染后的 DOM，解析 #__T 里的 JSON。
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
  var R = { alerts: [], cases: [], natives: [] };
  window.__T = R;
  // 原生弹窗一律记成违规（2026-10-23 起页面里不该再出现）
  window.alert = function(m){ R.natives.push('alert: ' + String(m)); };
  window.confirm = function(m){ R.natives.push('confirm: ' + String(m)); return true; };
  window.prompt = function(m){ R.natives.push('prompt: ' + String(m)); return ''; };
  // ★ 页面里的提示/确认框已经换成 askAlert / askConfirm（返回 Promise）。
  //   这套测的是"备用药"业务规则，弹窗本身有专门断言（test_box.py 第 20 节）——
  //   这里只把文案记下来，不真弹，免得每条断言都要替用户点一次按钮。
  askAlert = function(o){
    R.alerts.push(typeof o === 'string' ? o : ((o.title || '') + ' ' + (o.sub || '')));
    return Promise.resolve();
  };
  askConfirm = function(o){
    R.confirms = R.confirms || [];
    R.confirms.push(typeof o === 'string' ? o : ((o.title || '') + ' ' + (o.sub || '')));
    return Promise.resolve(true);
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
  function daysTxt(n){ var c = card(n); return c ? c.querySelector('.days .v').textContent.trim() : '(无卡片)'; }
  function unitTxt(n){ var c = card(n); var u = c && c.querySelector('.days .u'); return u ? u.textContent.trim() : '(无)'; }
  function metaTxt(n){
    var c = card(n);
    return c ? c.querySelector('.med-meta').textContent.replace(/\s+/g, ' ').trim() : '(无卡片)';
  }
  // 造一批药：2 味每天吃 + 2 味备用（都没写 kind 的一味用来验零迁移）
  function fixture(){
    var list = [
      { name:'每天吃甲片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
        stockBase:14, stockDate:agoD(13), rxMax:30, kind:'daily' },   // 扣到今天 = 1 片 / 1 天
      { name:'每天吃乙片', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
        stockBase:40, stockDate:todayStr(), rxMax:30, kind:'daily' }, // 20 天
      { name:'备用多片',  spec:'0.3g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
        stockBase:10, stockDate:agoD(20), kind:'prn' },              // 20 天前数的 10 片 → 仍是 10
      { name:'备用少片',  spec:'0.3g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
        stockBase:3,  stockDate:agoD(60), kind:'prn' },              // 60 天前也只有 3 → 仍是 3
      { name:'老存档片',  spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
        stockBase:30, stockDate:todayStr(), rxMax:30 }               // ★ 没有 kind 字段
    ];
    list.forEach(function(d){ d.id = uid(); });
    setMeds(list); save(); render();
  }

  // ---- 0. 零迁移：没有 kind 的老存档 = 每天吃 ----
  fixture();
  ck('老存档（没有 kind 字段）仍按「每天吃」算', kindOf(byName('老存档片')) === 'daily',
     kindOf(byName('老存档片')));
  ck('老存档那味照旧被扣（30 片基准 / 今天 → 30）',
     stockNow(byName('老存档片')) === 30, stockNow(byName('老存档片')));
  ck('isPrn() 对老存档返回 false', isPrn(byName('老存档片')) === false, isPrn(byName('老存档片')));

  // ---- 1. 「不跟天数走」的根：dailyUse 为 0，下游全被它关掉 ----
  var prnM = byName('备用多片');
  ck('备用药 dailyUse = 0（这是关掉"按天推"的总阀门）', dailyUse(prnM) === 0, dailyUse(prnM));
  ck('备用药 daysLeft = Infinity（没有"够吃几天"这回事）',
     daysLeft(prnM) === Infinity, String(daysLeft(prnM)));
  ck('备用药 level = prn', level(prnM) === 'prn', level(prnM));
  ck('备用药不进续方清单（inRefill=false）', inRefill(prnM) === false, inRefill(prnM));
  ck('备用药 needUnits = 0（不该补货）', needUnits(prnM) === 0, needUnits(prnM));
  ck('备用药不在「该开的药」清单里',
     refillItems().every(function(m){ return !isPrn(m); }),
     refillItems().map(function(m){ return m.name; }).join('/') || '(空)');
  ck('备用药不在「已经开药」的入库计划里',
     dispItems().every(function(m){ return !isPrn(m); }),
     dispItems().map(function(m){ return m.name; }).join('/') || '(空)');
  ck('「要开的药」那段文字里没有备用药',
     buildRefillText().indexOf('备用') < 0, buildRefillText().replace(/\n/g, ' / ') || '(空)');

  // ★ 关键：余量绝不自动往下扣（20 天前 / 60 天前数的，今天还是那个数）
  ck('备用药余量不随时间递减（20 天前数的 10 片，今天仍是 10）',
     stockNow(byName('备用多片')) === 10, stockNow(byName('备用多片')));
  ck('备用药余量不随时间递减（60 天前数的 3 片，今天仍是 3）',
     stockNow(byName('备用少片')) === 3, stockNow(byName('备用少片')));
  // 对照：每天吃的那味必须照旧被扣（否则上面两条可能是"全都停了"这种假绿）
  ck('对照：每天吃的那味照旧被扣（14 片 / 13 天前 → 1 片）',
     stockNow(byName('每天吃甲片')) === 1, stockNow(byName('每天吃甲片')));

  // ---- 2. 卡片：右上角写余量，不写天数；不打进度条 ----
  ck('备用药卡片右上角是「10」，不是天数', daysTxt('备用多片') === '10', daysTxt('备用多片'));
  ck('备用药卡片右下角小字是单位「片」', unitTxt('备用多片') === '片', unitTxt('备用多片'));
  ck('备用药卡片带「备用药」标签',
     !!card('备用多片') && /备用药/.test(card('备用多片').querySelector('.med-name').textContent),
     card('备用多片') ? card('备用多片').querySelector('.med-name').textContent.trim() : '(无)');
  ck('备用药卡片不画进度条（没有「还剩百分之几」这回事）',
     !card('备用多片').querySelector('.bar'), '');
  ck('备用药卡片说明了不按天算', /不按天算/.test(metaTxt('备用多片')), metaTxt('备用多片'));
  ck('备用药卡片不出现「每日/每 N 天」这类频次文字',
     !/每日|每\s*\d+\s*天/.test(metaTxt('备用多片')), metaTxt('备用多片'));
  ck('每天吃的卡片右上角仍是天数（回归）', daysTxt('每天吃甲片') === '1', daysTxt('每天吃甲片'));
  ck('每天吃的卡片仍画进度条', !!card('每天吃甲片').querySelector('.bar'), '');
  ck('备用药的卡片 data-lv=prn（配色跟档位区分开）',
     card('备用多片').getAttribute('data-lv') === 'prn', card('备用多片').getAttribute('data-lv'));

  // 余量为 0 的备用药：数字 0 + 小字「已用完」+ 提醒补货
  var zero = byName('备用少片');
  zero.stockBase = 0;
  save(); render();
  ck('备用药余量 0 → 右上角写 0、小字写「已用完」',
     daysTxt('备用少片') === '0' && unitTxt('备用少片') === '已用完',
     daysTxt('备用少片') + ' / ' + unitTxt('备用少片'));
  ck('备用药余量 0 → 卡片提醒「空了，记得补」', /空了/.test(metaTxt('备用少片')), metaTxt('备用少片'));
  ck('备用药空了也不进续方清单（它本来就不按天算）',
     !refillItems().some(function(m){ return m.name === '备用少片'; }), '');
  fixture();

  // ---- 3. 列表分成两组：每天吃的在前，备用药在后 ----
  var heads = qa('#list .sect-h').map(function(el){ return el.textContent.replace(/\s+/g, ' ').trim(); });
  ck('清单上有两个分组标题', heads.length === 2, heads.join(' | '));
  ck('第一个标题是「每天吃的药」', heads[0] && heads[0].indexOf('每天吃的药') === 0, heads[0]);
  ck('第二个标题是「备用药」并写明不按天算',
     heads[1] && heads[1].indexOf('备用药') === 0 && /不按天数算/.test(heads[1]), heads[1]);
  ck('标题上带条数（每天吃的 3 种 / 备用药 2 种）',
     /3 种/.test(heads[0]) && /2 种/.test(heads[1]), heads.join(' | '));
  var order = qa('#list .med .med-name').map(function(el){
    return el.textContent.replace(/\s+/g, ' ').trim();
  });
  // ⚠️ 卡片文字里带着档位标（「备用少片 备用药」），所以不能拿 indexOf 比整个字符串 —— 要按前缀找
  function posOf(nm){
    for(var k = 0; k < order.length; k++) if(order[k].indexOf(nm) === 0) return k;
    return -1;
  }
  var iLastDaily = Math.max.apply(null, order.map(function(t, i){
    return /每天吃|老存档/.test(t) ? i : -1;
  }));
  var iFirstPrn = order.map(function(t, i){ return /备用/.test(t) ? i : -1; })
                       .filter(function(i){ return i >= 0; })[0];
  ck('排序：所有"每天吃的"都排在"备用药"前面', iFirstPrn > iLastDaily,
     '最后一味每天吃 @' + iLastDaily + ' / 第一味备用 @' + iFirstPrn);
  ck('备用药之间按余量少的靠前（3 片在 10 片前面）', posOf('备用少片') < posOf('备用多片'),
     order.join(' / '));
  ck('提示语跟"天数"脱钩（没有药箱是空的）', !!q('#listHint').textContent, q('#listHint').textContent);

  // 只有备用药时：不该出现空着的「每天吃的药」标题
  setMeds([{ id: uid(), name:'只有备用药', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
             stockBase:5, stockDate:todayStr(), kind:'prn' }]);
  save(); render();
  var h2 = qa('#list .sect-h');
  ck('只有备用药时只显示一个分组标题', h2.length === 1, h2.length);
  ck('那个标题是备用药、而且没有加分隔线（.mt）',
     h2[0] && h2[0].textContent.indexOf('备用药') === 0 && !h2[0].className.match(/\bmt\b/), h2[0].className);
  fixture();

  // ---- 4. 统计栏：每天吃的三档只数每天吃的，备用药单独一格 ----
  var stats = qa('#stats .stat').map(function(el){
    return el.querySelector('.n').textContent.trim() + '=' + el.querySelector('.l').textContent.trim();
  });
  ck('统计栏有 5 格（每天吃的 / 已用完 / 偏少 / 充足 / 备用药）', stats.length === 5, stats.join(' | '));
  ck('「每天吃的药」那格数的是 3 条（备用药不算进去）', /^3=每天吃的药/.test(stats[0]), stats[0]);
  ck('「备用药」那格数的是 2 条', /^2=备用药/.test(stats[4]), stats[4]);
  // ★ 别拿"某格数字等于 2"去判"备用药没混进去" —— 这一批里「充足」正好也是 2，
  //   那种断言绿得毫无意义。真该验的是**不变量**：把一味备用药的余量归零，
  //   「已用完」那格不许跟着 +1（它只数每天吃的）。
  byName('备用多片').stockBase = 0; save(); render();
  var stats2 = qa('#stats .stat').map(function(el){
    return el.querySelector('.n').textContent.trim() + '=' + el.querySelector('.l').textContent.trim();
  });
  ck('备用药余量归零 → 「已用完」那格不动（它只数每天吃的）',
     stats2[1] === stats[1], stats[1] + ' → ' + stats2[1]);
  ck('备用药余量归零 → 「备用药」那格仍是 2（不按余量分档）',
     /^2=备用药/.test(stats2[4]), stats2[4]);
  ck('备用药余量归零 → 三档之和仍等于每天吃的药数',
     (function(){
       var n = function(s){ return Number(s.split('=')[0]); };
       return n(stats2[1]) + n(stats2[2]) + n(stats2[3]) === n(stats2[0]);
     })(), stats2.join(' | '));
  ck('三档之和 == 每天吃的药（账算得回来）', (function(){
    var n = function(s){ return Number(s.split('=')[0]); };
    var daily = n(stats[0]);
    // na（没设用法）本来就不单独占一格，只在没有 na 时要求相等
    return n(stats[1]) + n(stats[2]) + n(stats[3]) === daily;
  })(), stats.join(' | '));
  fixture();                       // 上面把「备用多片」归零过，还原回来

  // ---- 5. 表单：选「备用药」时，按天算的那几格收起来、也不拦保存 ----
  var fmed = q('#formMed');
  openEditor(byName('备用多片').id);
  ck('编辑备用药 → 类型单选落在「备用药」', fmed.kind.value === 'prn', fmed.kind.value);
  ck('编辑备用药 → 「每几天 / 几次」那几格收起来了',
     q('#doseCycleWrap').style.display === 'none', q('#doseCycleWrap').style.display);
  ck('编辑备用药 → 「开药限制」整块收起来',
     q('#secRxLimit').style.display === 'none' && q('#rxMaxLabel').style.display === 'none',
     q('#secRxLimit').style.display + ' / ' + q('#rxMaxLabel').style.display);
  ck('编辑备用药 → 单选卡高亮跟着走（.kind.on 落在 prn 那张）',
     qa('#kindRow .kind.on').length === 1
     && qa('#kindRow .kind.on')[0].getAttribute('data-kind') === 'prn',
     qa('#kindRow .kind.on').map(function(e){ return e.getAttribute('data-kind'); }).join(','));
  ck('编辑备用药 → 余量预填的就是卡片上那个数（10）',
     Math.abs(num(fmed.stockBase.value) - 10) < 1e-9, fmed.stockBase.value);
  ck('编辑备用药 → 用法预览说明它不按天算', /不按天算/.test(q('#dosePreview').textContent),
     q('#dosePreview').textContent);
  // 必填口径：备用药只要 名称 / 单位 / 余量 / 日期
  ck('编辑备用药 → 每次用量 / 每几天 / 几次 / 开药上限都不再 required',
     !fmed.perDose.required && !fmed.cycleDays.required && !fmed.timesPerCycle.required
     && !fmed.rxMax.required,
     [fmed.perDose.required, fmed.cycleDays.required, fmed.timesPerCycle.required,
      fmed.rxMax.required].join('/'));
  ck('编辑备用药 → 必填说明改口（不再说"这四格都是必填"）',
     /备用药只记/.test(q('#doseTip').textContent) && /备用药只要/.test(q('#reqLegend').textContent),
     q('#doseTip').textContent);

  // 切回「每天吃」→ 那几格又回来、required 也回来（不能一去不回）
  fmed.kind.value = 'daily'; syncKindUI();
  ck('切回「每天吃」→ 那几格重新显示', q('#doseCycleWrap').style.display === 'contents',
     q('#doseCycleWrap').style.display);
  ck('切回「每天吃」→ 开药限制那格重新显示并要求必填',
     q('#rxMaxLabel').style.display !== 'none' && fmed.rxMax.required === true,
     q('#rxMaxLabel').style.display + ' / ' + fmed.rxMax.required);
  ck('切回「每天吃」→ 必填说明也切回去', /只有/.test(q('#reqLegend').textContent),
     q('#reqLegend').textContent);
  q('#dlgMed').close();

  // ---- 6. 存一条备用药：按天算的那几格**空着**也得能存 ----
  var nBefore = meds.length;
  openEditor(null);
  fmed.kind.value = 'prn'; syncKindUI();
  fmed.name.value = '新备用片';
  fmed.spec.value = '0.2g';
  fmed.perDose.value = 1;
  fmed.unit.value = '片';
  fmed.cycleDays.value = '';          // ★ 故意空着：每天吃的这时会被拦
  fmed.timesPerCycle.value = '';      // ★ 同上
  fmed.stockBase.value = 12;
  fmed.stockDate.value = todayStr();
  fmed.rxMax.value = '';              // 备用药用不上这格
  ck('备用药表单在「每几天 / 几次 / 开药上限」都空着时仍然合法（浏览器那一关）',
     q('#formMed').checkValidity() === true, q('#formMed').checkValidity());
  var al0 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('备用药存进去了（没被"每几天几次"卡住）', meds.length === nBefore + 1, meds.length - nBefore);
  ck('没有弹任何"还差这几项"', R.alerts.length === al0, R.alerts.slice(al0).join(' | ') || '(没弹)');
  var np = byName('新备用片');
  ck('存下来的 kind = prn', !!np && np.kind === 'prn', np ? np.kind : '(没进来)');
  ck('存下来的 cycles 被写成 1 天 / 0 次（"没有每日计划"落在数据里）',
     !!np && np.cycleDays === 1 && np.timesPerCycle === 0,
     np ? np.cycleDays + ' / ' + np.timesPerCycle : '(没进来)');
  ck('存下来的 rxMax = 0（备用药不按天数补货）', !!np && np.rxMax === 0, np ? np.rxMax : '(没进来)');
  ck('存下来的余量 = 12', !!np && np.stockBase === 12, np ? np.stockBase : '(没进来)');
  ck('新存的备用药当场出现在备用药那一组',
     (function(){
       var cs = qa('#list .med');
       var el = cs[cs.length - 1];
       return el && el.getAttribute('data-lv') === 'prn';
     })(), '');

  // 名字空着 → 照样拦（备用药也得知道是什么药）
  openEditor(null);
  fmed.kind.value = 'prn'; syncKindUI();
  fmed.name.value = '';
  fmed.unit.value = '片';
  fmed.stockBase.value = 1;
  fmed.stockDate.value = todayStr();
  var n2 = meds.length, al1 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('备用药名称空着 → 被拦下', meds.length === n2, meds.length - n2);
  ck('提示点名「药品名称」', /药品名称/.test(R.alerts.slice(al1).join(' | ')),
     R.alerts[R.alerts.length - 1]);
  q('#dlgMed').close();

  // ---- 7. 回归：每天吃的药那条必填口径一点没松 ----
  openEditor(null);
  ck('新增（默认每天吃）→ 整表不合法（开药上限空着）',
     q('#formMed').checkValidity() === false, q('#formMed').checkValidity());
  fmed.name.value = '回归用片';
  fmed.unit.value = '片';
  fmed.perDose.value = 1;
  fmed.cycleDays.value = 1;
  fmed.timesPerCycle.value = 1;
  fmed.stockBase.value = 1;
  fmed.stockDate.value = todayStr();
  fmed.rxMax.value = '';
  var n3 = meds.length, al2 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('每天吃的缺开药上限 → 仍被拦下', meds.length === n3, meds.length - n3);
  ck('提示仍是「一次最多能开几天」（2026-10-27 改成按天填）',
     /一次最多能开几天/.test(R.alerts.slice(al2).join(' | ')), R.alerts[R.alerts.length - 1]);
  // 每周期几次填 0（= 没设用法）→ 仍拦
  fmed.rxMax.value = '30';
  fmed.timesPerCycle.value = 0;
  var al3 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('每天吃的「每周期几次」填 0 → 仍被拦下', meds.length === n3, meds.length - n3);
  ck('提示仍是「每周期几次（要大于 0）」',
     /每周期几次（要大于 0）/.test(R.alerts.slice(al3).join(' | ')), R.alerts[R.alerts.length - 1]);
  q('#dlgMed').close();

  // ---- 8. 改类型：每天吃 → 备用药，同一味药换套口径 ----
  // 造一味「14 片 / 13 天前 / 每日 1 片」→ 现 1 片、剩 1 天（偏少）
  setMeds([{ id: uid(), name:'转换用片', spec:'5mg', perDose:1, unit:'片', cycleDays:1,
             timesPerCycle:1, stockBase:14, stockDate:agoD(13), rxMax:30, kind:'daily' }]);
  save(); render();
  ck('转换前：剩 1 天、判为偏少', daysLeft(byName('转换用片')) === 1 && level(byName('转换用片')) === 'soon',
     daysLeft(byName('转换用片')) + ' / ' + level(byName('转换用片')));
  openEditor(byName('转换用片').id);
  fmed.kind.value = 'prn'; syncKindUI();
  submitMed({ preventDefault: function(){} });
  var cv = byName('转换用片');
  ck('改成备用药后 kind = prn', cv.kind === 'prn', cv.kind);
  ck('改成备用药后不再算天数（level = prn、不进续方）',
     level(cv) === 'prn' && !inRefill(cv), level(cv) + ' / ' + inRefill(cv));
  ck('改成备用药后余量停在 1 片、不再往下扣', stockNow(cv) === 1, stockNow(cv));
  ck('改成备用药后卡片换成余量显示', daysTxt('转换用片') === '1', daysTxt('转换用片'));
  // 再改回每天吃 → 又按天数算（余量仍是 1，剩 1 天）
  openEditor(cv.id);
  fmed.kind.value = 'daily'; syncKindUI();
  fmed.rxMax.value = '30';
  fmed.timesPerCycle.value = 1;
  submitMed({ preventDefault: function(){} });
  cv = byName('转换用片');
  ck('改回每天吃 → kind = daily 且又开始算天数',
     cv.kind === 'daily' && isFinite(daysLeft(cv)), cv.kind + ' / ' + daysLeft(cv));

  // ---- 9. 批量调存量：备用药按不了天，一根手指都不许碰 ----
  fixture();
  var upRows = {}, dnRows = {};
  bulkRows(7, 1).forEach(function(r){ upRows[r.m.name] = r; });
  bulkRows(7, -1).forEach(function(r){ dnRows[r.m.name] = r; });
  ck('批量：备用药被标成 skip', upRows['备用多片'].skip === true && dnRows['备用多片'].skip === true, '');
  ck('批量：备用药调整量是 0、数量没被换过',
     upRows['备用多片'].delta === 0 && upRows['备用多片'].next === 10,
     upRows['备用多片'].delta + ' / ' + upRows['备用多片'].next);
  ck('批量：每天吃的那味照旧能被换（14→1，+7 天 = 8）',
     upRows['每天吃甲片'].skip === false && upRows['每天吃甲片'].next === 8,
     upRows['每天吃甲片'].next);
  openBulk();
  var bulkTxt = q('#bulkList').textContent;
  ck('批量弹窗里备用药那行写「备用药，不按天算」（不是含糊的"没法按天算"）',
     /备用药，不按天算/.test(bulkTxt), '');
  ck('批量弹窗里备用药也标了「备用药」文字标', /备用药/.test(bulkTxt), '');
  var ckPrn = qa('#bulkList input.bulk-ck').filter(function(c){
    return c.disabled === true; }).length;
  ck('批量弹窗里备用药的勾选框是禁用的', ckPrn === 2, ckPrn);
  q('#dlgBulk').close();
  // 真跑一次「全选 + 减 7 天」：备用药必须一动不动
  openBulk();
  q('#btnBulkAll').click();
  var before10 = { a: byName('备用多片').stockBase, b: byName('备用少片').stockBase };
  applyBulk(-1);
  ck('批量减完之后备用药一点没动',
     byName('备用多片').stockBase === before10.a && byName('备用少片').stockBase === before10.b,
     before10.a + '→' + byName('备用多片').stockBase + ' / ' + before10.b + '→' + byName('备用少片').stockBase);
  ck('同一批里每天吃的那味确实被减了',
     byName('每天吃乙片').stockBase < 40, byName('每天吃乙片').stockBase);

  // ---- 10. 「又买了药 / 校准余量」对备用药照常生效（余量只能这样改）----
  fixture();
  var pm = byName('备用多片');
  restockMed(pm.id);
  q('#numVal').value = '5';
  submitNum({ preventDefault: function(){} });
  ck('备用药「又买了药」：10 + 5 = 15', byName('备用多片').stockBase === 15,
     byName('备用多片').stockBase);
  ck('备用药「又买了药」后基准日滚到今天', byName('备用多片').stockDate === todayStr(),
     byName('备用多片').stockDate);
  calibMed(byName('备用多片').id);
  q('#numVal').value = '2';
  submitNum({ preventDefault: function(){} });
  ck('备用药「校准余量」：改成 2', byName('备用多片').stockBase === 2, byName('备用多片').stockBase);
  ck('校准后卡片跟着变 2', daysTxt('备用多片') === '2', daysTxt('备用多片'));

  // ---- 11. 按人分开：一个人的备用药不会串到另一个人身上 ----
  fixture();
  var keepId = currentId;
  var yi = { id: uid(), name:'乙', note:'', meds:[
    { id: uid(), name:'乙的备用片', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
      stockBase:4, stockDate:todayStr(), kind:'prn' }
  ]};
  people.push(yi); save(); switchPerson(yi.id);
  ck('切到「乙」→ 只看到乙自己的 1 味备用药', meds.length === 1 && isPrn(meds[0]),
     meds.length + ' / ' + kindOf(meds[0]));
  var s2 = qa('#stats .stat .n').map(function(e){ return e.textContent.trim(); });
  ck('乙的统计栏：每天吃的 0、备用药 1', s2[0] === '0' && s2[4] === '1', s2.join('/'));
  switchPerson(keepId);

  // ---- 12. 导出 / 导入往返：kind 不许在路上丢 ----
  fixture();
  function prnCountIn(list){
    return (list || []).filter(function(m){ return m.kind === 'prn'; }).length;
  }
  // ★ 只数「当前这个人」的 —— people 里还留着上面建的「乙」，全表数会把她的算进来
  var snap = JSON.parse(JSON.stringify({ v: 2, currentId: currentId, people: people }));
  var curInSnap = snap.people.filter(function(p){ return p.id === snap.currentId; })[0];
  ck('导出快照里当前人的备用药带着 kind=prn（2 条）', prnCountIn(curInSnap.meds) === 2,
     prnCountIn(curInSnap.meds));
  var back = migrate(snap);
  var curInBack = back.people.filter(function(p){ return p.id === snap.currentId; })[0];
  ck('导入后 kind 还在（migrate 不会把类型洗掉）', prnCountIn(curInBack.meds) === 2,
     prnCountIn(curInBack.meds));
  ck('导入后备用药的 cycleDays/timesPerCycle 也原样（1 天 / 0 次）',
     (function(){
       var p = curInBack.meds.filter(function(m){ return m.name === '备用多片'; })[0];
       return !!p && p.cycleDays === 1 && p.timesPerCycle === 0;
     })(), '');

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
    tmpdir = tempfile.mkdtemp(prefix="dk_prn_")
    page = os.path.join(tmpdir, "t.html")
    open(page, "w", encoding="utf-8").write(src[:i] + PROBE + src[i:])

    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--virtual-time-budget=6000",
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
