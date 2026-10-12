# -*- coding: utf-8 -*-
"""
drug-keeper：「需补」是否跟着单次开药上限走 —— 真实 DOM 驱动测试。

做法：把 index.html 复制成一份临时副本，在 </body> 前注入检查脚本，
用无头 Chrome --dump-dom 取回渲染后的真实 DOM，解析 #__T 里的 JSON 结果。

断言口径（2026-09-28 用户要求）：一次挂号只能开一次药 → 卡片上的「需补」
与实际入库量必须是**同一个数**，并且受单次上限约束（有上限 → 上限值）。
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
/* ===== 注入的测试代码（必须插在主 IIFE 内部，否则 meds/各函数都看不见）===== */
(function(){
  var R = { alerts: [], cases: [], prompts: [] };
  window.__T = R;
  window.alert = function(m){ R.alerts.push(String(m)); };
  // ★ 记录原生 prompt 有没有被调用过：2026-10-11 用户要求「又买了药 / 校准余量」
  //   不许再用浏览器自带的小框，必须走中间弹窗。这里把它换成只记账、不弹的桩。
  window.prompt = function(m){ R.prompts.push(String(m)); return null; };
  function q(s){ return document.querySelector(s); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function card(name){
    var cs = document.querySelectorAll('#list .med'), i;
    for(i = 0; i < cs.length; i++){
      var n = cs[i].querySelector('.med-name');
      if(n && n.textContent.indexOf(name) >= 0) return cs[i];
    }
    return null;
  }
  function needTxt(name){
    var c = card(name);
    if(!c) return '(无卡片)';
    var el = c.querySelector('.need');
    return el ? el.textContent.replace(/\s+/g, ' ').trim() : '(无 .need)';
  }
  function byName(name){
    for(var i = 0; i < meds.length; i++) if(meds[i].name === name) return meds[i];
    return null;
  }

  // ---- 0. 前置：自注入示例数据（程序内的 loadDemo 已按开源要求删除，测试自己造）----
  var agoD = function(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); };
  var demo = [
    { name:'维生素C片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:7 },
    { name:'维生素B2片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:21, stockDate:agoD(18), rxMax:14 },
    { name:'葡萄糖酸钙片', spec:'0.5g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
      stockBase:60, stockDate:agoD(20) },
    { name:'健胃消食片', spec:'0.8g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:90, stockDate:agoD(10) },
    { name:'维生素B12注射液', spec:'0.5mg/支', perDose:1, unit:'支', cycleDays:14, timesPerCycle:1,
      stockBase:2, stockDate:agoD(20), rxMax:2 }
  ];
  demo.forEach(function(d){ d.id = uid(); });
  setMeds(demo);
  save(); render();
  ck('自注入 5 种药后渲染成功', meds.length === 5, meds.length);

  // ---- 1. 有单次上限、且一次开不完：入库量 = 上限，不是理论缺口 ----
  var a = byName('维生素C片');
  ck('维生素C 理论缺口=29', a && needUnits(a) === 29, a ? needUnits(a) : null);
  ck('维生素C 单次限=7', a && rxCap(a) === 7, a ? rxCap(a) : null);
  ck('维生素C 需补=7（跟单次限）', a && dispQty(a) === 7, a ? dispQty(a) : null);
  ck('维生素C 得开 5 次', a && rxTrips(a) === 5, a ? rxTrips(a) : null);

  // ---- 2. 有上限、需求刚好 <= 上限：入库量 = 上限（一次挂号开满）----
  var e = byName('维生素B12注射液');
  ck('维生素B12 理论缺口=2', e && needUnits(e) === 2, e ? needUnits(e) : null);
  ck('维生素B12 需补=2（=上限）', e && dispQty(e) === 2, e ? dispQty(e) : null);

  // ---- 3. 没设单次上限：入库量 = 补满目标天数 ----
  var m = byName('健胃消食片');            // 无 rxMax，示例里余量大、不在清单
  if(m){ m.stockBase = 0; m.stockDate = todayStr(); }
  render();
  ck('健胃消食 无上限 需补=30', m && dispQty(m) === 30, m ? dispQty(m) : null);

  // ---- 3b. ★ 卡片上不应该再有「需补 / 单次限 / 得开 N 次」----
  var cards = document.querySelectorAll('#list .med');
  var withNeed = document.querySelectorAll('#list .need').length;
  ck('卡片里没有任何 .need 提示', withNeed === 0, withNeed);
  var txtAll = '';
  for(var ci = 0; ci < cards.length; ci++) txtAll += cards[ci].textContent.replace(/\s+/g, ' ');
  ck('卡片文案不含「需补」', txtAll.indexOf('需补') < 0, txtAll.slice(0, 160));
  ck('卡片文案不含「单次限」', txtAll.indexOf('单次限') < 0, '');
  ck('卡片文案不含「得开」', txtAll.indexOf('得开') < 0, '');
  ck('卡片仍保留「余量约」', /余量约/.test(txtAll), '');
  // 颜色信号还在：维生素C只剩 1 天 → 偏少(soon)，卡片 data-lv 应为 soon
  var c1 = card('维生素C片');
  ck('维生素C卡片是偏少色', c1 && c1.getAttribute('data-lv') === 'soon',
     c1 ? c1.getAttribute('data-lv') : '(无卡片)');

  // ---- 3c. 只留三档文案：已用完 / 偏少 / 充足 ----
  // ★ 不能用 document.body.textContent 直接扫关键词：主 <script> 就在 body 里，
  //   textContent 会把整段 JS 源码（含注释里的旧词）一起算进来 —— 必假阳性。
  //   先克隆一份 body 并把 script/style 摘掉，才拿到「用户真正看得到的字」。
  var clone = document.body.cloneNode(true);
  var gone = clone.querySelectorAll('script, style'), gi;
  for(gi = 0; gi < gone.length; gi++) gone[gi].parentNode.removeChild(gone[gi]);
  var bodyTxt = clone.textContent.replace(/\s+/g, ' ');
  ck('可见文字里无「告急」', bodyTxt.indexOf('告急') < 0, bodyTxt.slice(0, 200));
  ck('可见文字里无「将尽」', bodyTxt.indexOf('将尽') < 0, '');
  ck('可见文字里无「不多」', bodyTxt.indexOf('不多') < 0, '');
  ck('可见文字里无「紧急」', bodyTxt.indexOf('紧急') < 0, '');
  ck('统计栏用「偏少（≤N天）」', /偏少（≤\d+天）/.test(bodyTxt), bodyTxt.slice(0, 240));
  ck('统计栏含「已用完」「充足」', bodyTxt.indexOf('已用完') >= 0 && bodyTxt.indexOf('充足') >= 0, '');
  ck('卡片 tag 是「偏少」', /维生素C片\s*偏少/.test(bodyTxt), '');
  // 硬断言：卡片上出现的档位文字只能是这几档
  var tags = [], ts = document.querySelectorAll('#list .med-name .tag'), ti;
  for(ti = 0; ti < ts.length; ti++) tags.push(ts[ti].textContent.trim());
  var uniq = tags.filter(function(v, i, a){ return a.indexOf(v) === i; });
  var ALLOW = ['偏少', '已用完', '充足', '未设用法'];
  ck('卡片 tag 只用这三档（+未设用法）',
     uniq.length > 0 && uniq.every(function(v){ return ALLOW.indexOf(v) >= 0; }), uniq.join('/'));

  // ---- 3d. ★ 用户报的 bug：设置里的「偏少线」必须就是统计栏那个 N ----
  // 根因：三档化时把内层的「告急线(urgentDays=7)」误挂上了「偏少线」这个名字，
  //   而统计栏/清单/卡片文字用的是 alertDays(14) —— 名字指向 7、事实边界是 14，自相矛盾。
  // 用户定稿：**不要第二条线**，只留一条「偏少线」= alertDays；颜色也只剩一种（黄）。
  var labName = function(nm){
    var el = document.querySelector('#formSet input[name="' + nm + '"]');
    var lab = el && el.closest ? el.closest('label') : null;
    if(!lab) return '(没找到)';
    for(var n = lab.firstChild; n; n = n.nextSibling){
      if(n.nodeType === 3 && n.textContent.trim()) return n.textContent.trim();
    }
    return '(空标签)';
  };
  ck('设置里「偏少线」绑的输入是 alertDays', labName('alertDays').indexOf('偏少线') === 0,
     labName('alertDays'));
  ck('第二条线的输入框已删除（无 urgentDays）',
     !document.querySelector('#formSet input[name="urgentDays"]'), '');
  ck('可见文字无「更少线」', bodyTxt.indexOf('更少线') < 0, '');
  ck('可见文字无「提醒线」', bodyTxt.indexOf('提醒线') < 0, '');
  var mStat = bodyTxt.match(/偏少（≤(\d+)天）/);
  ck('统计栏「偏少（≤N天）」的 N == 偏少线',
     !!mStat && Number(mStat[1]) === settings.alertDays,
     mStat ? mStat[1] + ' vs alertDays=' + settings.alertDays : '(没匹配到)');
  // 只剩一档：任何药都不该再判出 urgent（橙）
  var lvSet = {};
  meds.forEach(function(m){ lvSet[level(m)] = 1; });
  ck('等级里已无 urgent', !lvSet.urgent, Object.keys(lvSet).join('/'));

  // 这条线是唯一的事实边界：改它，统计栏与清单必须同时跟着变
  var fs = q('#formSet');
  openSettings(); fs.alertDays.value = 30; fs.targetDays.value = 30;
  submitSettings({ preventDefault: function(){} });
  ck('偏少线改 30 → 统计栏随之显示 ≤30 天',
     /偏少（≤30天）/.test(q('#stats').textContent), q('#stats').textContent.slice(0, 120));
  openSettings(); fs.alertDays.value = 14; fs.targetDays.value = 30;
  submitSettings({ preventDefault: function(){} });
  ck('设置已恢复 14', settings.alertDays === 14, settings.alertDays);
  ck('settings 里已无 urgentDays 字段', settings.urgentDays === undefined,
     String(settings.urgentDays));

  // ---- 4. 弹窗入库量必须与内部口径同一个数（弹窗里保留说明）----
  var plan = dispPlan();
  var pm = {};
  plan.forEach(function(p){ pm[p.m.name] = p.qty; });
  ck('弹窗计划含 5 种药（健胃消食被改成 0 余量后也进清单）', plan.length === 5, plan.length);
  ck('弹窗 维生素C入库=7', pm['维生素C片'] === 7, pm['维生素C片']);
  ck('弹窗 维生素B2入库=14', pm['维生素B2片'] === 14, pm['维生素B2片']);
  ck('弹窗 维生素B12入库=2', pm['维生素B12注射液'] === 2, pm['维生素B12注射液']);

  openDisp();
  var sub = q('#dispSub') ? q('#dispSub').textContent : '';
  var rows = q('#dispList') ? q('#dispList').textContent.replace(/\s+/g, ' ').trim() : '';
  ck('弹窗说明点明「一次挂号只能开一次药」', sub.indexOf('一次挂号只能开一次药') >= 0, sub);
  ck('弹窗行内说明按上限开满', /单次限\s*7 .*?按上限开满/.test(rows), rows.slice(0, 200));
  ck('弹窗行内说明给出趟数', /共需\s*29.*?要开\s*5\s*次/.test(rows), rows.slice(0, 300));

  // ---- 5. 真点一次「已经开药」，看库存是不是按上限加 ----
  var before = {
    a: stockNow(byName('维生素C片')),
    b: stockNow(byName('维生素B2片')),
    c: stockNow(byName('维生素B12注射液'))
  };
  submitDisp({ preventDefault: function(){} });
  var after = {
    a: byName('维生素C片').stockBase,
    b: byName('维生素B2片').stockBase,
    c: byName('维生素B12注射液').stockBase
  };
  ck('入库后 维生素C = 1+7 = 8', after.a === 8, JSON.stringify([before.a, after.a]));
  ck('入库后 维生素B2 = 3+14 = 17', after.b === 17, JSON.stringify([before.b, after.b]));
  ck('入库后 维生素B12 = 1+2 = 3', after.c === 3, JSON.stringify([before.c, after.c]));
  ck('提示还剩 1 种没到位', /还有\s*1\s*种/.test(R.alerts.join(' | ')), R.alerts.join(' | '));

  // ---- 6. 缺药但补满后离开清单 ----
  var asp = byName('健胃消食片');
  ck('健胃消食补满后 30 天、已不在清单',
     asp && daysLeft(asp) > settings.alertDays && !inRefill(asp), asp ? daysLeft(asp) : null);

  // ---- 7. 批量调整存量（整个药箱统一 ±N 天）----
  // 重置到已知状态（前面几段动过数据），并补一味「没设用法」的药来验跳过分支。
  var demo2 = [
    { name:'维生素C片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:7 },                  // 现 1，每日 1
    { name:'维生素B2片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:21, stockDate:agoD(18), rxMax:14 },                 // 现 3，每日 1
    { name:'葡萄糖酸钙片', spec:'0.5g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
      stockBase:60, stockDate:agoD(20) },                           // 现 20，每日 2 → 7 天=14 片
    { name:'健胃消食片', spec:'0.8g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:90, stockDate:agoD(10) },                           // 现 80，每日 1
    { name:'维生素B12注射液', spec:'0.5mg/支', perDose:1, unit:'支', cycleDays:14, timesPerCycle:1,
      stockBase:2, stockDate:agoD(20), rxMax:2 },                   // 现 1，每 14 天 1 支 → 7 天=0.5 支
    { name:'维生素E软胶囊', spec:'400IU', perDose:1, unit:'粒', cycleDays:1, timesPerCycle:0,
      stockBase:30, stockDate:todayStr() }                          // 没设用法 → 应被跳过
  ];
  demo2.forEach(function(d){ d.id = uid(); });
  setMeds(demo2); save(); render();
  ck('批量前置：6 味药已就位', meds.length === 6, meds.length);

  // --- 7a. 纯计算：天数 → 数量，各按自己的用法换算 ---
  var upR = {}, dnR = {};
  bulkRows(7, 1).forEach(function(r){ upR[r.m.name] = r; });
  bulkRows(7, -1).forEach(function(r){ dnR[r.m.name] = r; });
  ck('维生素C 现 1 片 → 加 7 天 = 8 片', upR['维生素C片'].next === 8, upR['维生素C片'].next);
  ck('维生素C 减 7 天 → 0 且标记已截断',
     dnR['维生素C片'].next === 0 && dnR['维生素C片'].floored === true,
     JSON.stringify(dnR['维生素C片']));
  ck('葡萄糖酸钙 每日 2 片 → 加 7 天 = 34（+14）', upR['葡萄糖酸钙片'].next === 34,
     upR['葡萄糖酸钙片'].next);
  ck('维生素B12 每 14 天 1 支 → 加 7 天 = 1.5（+0.5）', upR['维生素B12注射液'].next === 1.5,
     upR['维生素B12注射液'].next);
  ck('健胃消食 现 80 → 加 7 天 = 87', upR['健胃消食片'].next === 87, upR['健胃消食片'].next);
  ck('没设用法的那味被跳过', upR['维生素E软胶囊'].skip === true && dnR['维生素E软胶囊'].skip === true, '');
  ck('跳过的药不会被改动（next 仍 30）', upR['维生素E软胶囊'].next === 30, upR['维生素E软胶囊'].next);

  // --- 7b. 弹窗预览（2026-10-12：可勾选 + 顺序同主页 + 档位色）---
  // 这一段的 demo2 正好能验顺序：录入顺序是 氨氯/阿托/二甲/健胃消食/维生素B12/维D，
  // 而按档位排完后 维生素B12（每 14 天 1 支，剩 14 天 = 偏少）要排到健胃消食（剩 80 天 = 充足）前面。
  function namesOf(sel){
    return Array.prototype.map.call(document.querySelectorAll(sel), function(el){
      var t = el.firstChild;
      return t ? String(t.nodeValue).trim() : '';
    });
  }
  openBulk();
  var brows = document.querySelectorAll('#bulkList .disp-row');
  ck('弹窗列出全部 6 味（含跳过的那味）', brows.length === 6, brows.length);
  ck('标题写清作用对象是当前人', /「我」药箱/.test(q('#bulkSub').textContent), q('#bulkSub').textContent);
  var body = q('#bulkList').textContent.replace(/\s+/g, ' ');
  ck('预览同时给出加/减两个方向的数', /＋7 天 → 8 片/.test(body) && /−7 天/.test(body), body.slice(0, 200));
  ck('预览标出会被减到 0 的药', /已减到 0/.test(body), '');
  ck('预览标出没设用法的药', /没法按天算/.test(body), '');

  // ★ 顺序必须和主页一模一样
  var homeOrder = namesOf('#list .med .med-name');
  var bulkOrder = namesOf('#bulkList .disp-row .disp-name');
  ck('弹窗里的排列顺序和主页一致',
     homeOrder.length === 6 && homeOrder.join('|') === bulkOrder.join('|'),
     '主页 ' + homeOrder.join(' / ') + '　弹窗 ' + bulkOrder.join(' / '));
  ck('排序确实按档位重排了（维生素B12排在健胃消食前面，不是录入顺序）',
     bulkOrder.indexOf('维生素B12注射液') < bulkOrder.indexOf('健胃消食片'),
     bulkOrder.join(' / '));

  // ★ 颜色：行上有档位标记，且档位和主页的 level() 一致
  function lvCount(sel, v){
    return document.querySelectorAll(sel + '[data-lv=' + v + ']').length;
  }
  ck('偏少（soon）的 4 味行上带 soon 标记', lvCount('#bulkList .disp-row', 'soon') === 4,
     lvCount('#bulkList .disp-row', 'soon'));
  ck('充足的 1 味带 ok 标记', lvCount('#bulkList .disp-row', 'ok') === 1,
     lvCount('#bulkList .disp-row', 'ok'));
  ck('没设用法的 1 味带 na 标记', lvCount('#bulkList .disp-row', 'na') === 1,
     lvCount('#bulkList .disp-row', 'na'));
  ck('每行还挂了「已用完 / 偏少 / 充足 / 未设用法」文字标',
     document.querySelectorAll('#bulkList .disp-name .tag').length === 6,
     document.querySelectorAll('#bulkList .disp-name .tag').length);

  // ★ 勾选框：默认**只勾「已用完 / 偏少」**的（充足的不勾），没设用法的勾不了
  //   2026-10-12 用户要求：「默认打钩的是已用完和偏少的 充足的不用打勾」。
  function ckOf(n){
    return document.querySelector('#bulkList input.bulk-ck[data-id="' + byName(n).id + '"]');
  }
  function afterOf(n){ return q('#bulkAfter-' + byName(n).id).textContent.trim(); }
  function setCk(n, on){
    var c = ckOf(n);
    c.checked = on;
    c.dispatchEvent(new Event('change', {bubbles:true}));
  }
  var cks = document.querySelectorAll('#bulkList input.bulk-ck');
  var nCk = Array.prototype.filter.call(cks, function(c){ return c.checked; }).length;
  var nDis = Array.prototype.filter.call(cks, function(c){ return c.disabled; }).length;
  ck('每行都有勾选框', cks.length === 6, cks.length);
  ck('默认勾上 4 味（4 味偏少）、禁用 1 味（没设用法）', nCk === 4 && nDis === 1, nCk + ' / ' + nDis);
  ck('「偏少」的默认勾上（维生素B12）', ckOf('维生素B12注射液').checked === true, '');
  ck('「偏少」的默认勾上（维生素C）', ckOf('维生素C片').checked === true, '');
  ck('「充足」的默认不勾（健胃消食）', ckOf('健胃消食片').checked === false, '');
  ck('「充足」的默认不勾 → 那行只写「未勾选」、不给数字', afterOf('健胃消食片') === '未勾选',
     afterOf('健胃消食片'));
  ck('底部写清已勾选几种（4 / 5）', /已勾选 4 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('按钮带上了天数与实数', /勾选的 4 种各增加 7 天/.test(q('#btnBulkPlus').textContent)
     && /勾选的 4 种各减少 7 天/.test(q('#btnBulkMinus').textContent),
     q('#btnBulkPlus').textContent);
  ck('说明里写清了默认勾选规则', /默认只勾「已用完 \/ 偏少」/.test(q('#bulkSub').textContent),
     q('#bulkSub').textContent);
  q('#bulkDays').value = 14; renderBulk();
  ck('改天数 → 按钮与预览跟着变', /勾选的 4 种各增加 14 天/.test(q('#btnBulkPlus').textContent),
     q('#btnBulkPlus').textContent);
  q('#bulkDays').value = 7; renderBulk();

  // ★ 默认不勾 ≠ 调不了：自己把「充足」那味勾上，它照样能一起调
  setCk('健胃消食片', true);
  ck('手动勾上充足的药 → 已勾选 5 / 5', /已勾选 5 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('手动勾上后那行照常给数字', /＋7 天/.test(afterOf('健胃消食片')), afterOf('健胃消食片'));

  // ★ 取消勾选一味（偏少的） → 那一行不再给数字，汇总与按钮当场跟着变
  setCk('葡萄糖酸钙片', false);
  ck('取消勾选后那行写「未勾选」，不再给数字', afterOf('葡萄糖酸钙片') === '未勾选',
     afterOf('葡萄糖酸钙片'));
  ck('取消勾选 → 底部变成已勾选 4 / 5', /已勾选 4 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('取消勾选 → 按钮文案变成 4 种', /勾选的 4 种各增加 7 天/.test(q('#btnBulkPlus').textContent),
     q('#btnBulkPlus').textContent);
  ck('没被取消的行照旧给数字', /＋7 天/.test(afterOf('维生素C片')), afterOf('维生素C片'));

  // ★ 全不选 → 两个确认按钮禁用（避免"什么都没选就点确认"）
  q('#btnBulkNone').click();
  ck('全不选 → 已勾选 0 / 5', /已勾选 0 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('全不选 → 两个确认按钮都禁用',
     q('#btnBulkPlus').disabled === true && q('#btnBulkMinus').disabled === true,
     q('#btnBulkPlus').disabled + ' / ' + q('#btnBulkMinus').disabled);
  ck('全不选 → 没设用法那行仍写「没法按天算」',
     /没法按天算/.test(q('#bulkList').textContent), '');

  // ★ 全选 → 再勾回来（补回取消的那味）
  q('#btnBulkAll').click();
  ck('全选 → 又回到已勾选 5 / 5', /已勾选 5 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('全选 → 按钮恢复',
     q('#btnBulkPlus').disabled === false && /勾选的 5 种/.test(q('#btnBulkPlus').textContent), '');
  ck('全选不会把「没设用法」那味的禁用框勾上',
     (function(){
       var c = document.querySelector('#bulkList input.bulk-ck[data-id="'
         + byName('维生素E软胶囊').id + '"]');
       return c.disabled === true && c.checked === false;
     })(), '');

  // --- 7c. 真的执行：增加 ---
  var alertsBefore = R.alerts.length;
  applyBulk(1);
  ck('加 7 天：维生素C 8', byName('维生素C片').stockBase === 8,
     byName('维生素C片').stockBase);
  ck('加 7 天：维生素B2 10', byName('维生素B2片').stockBase === 10,
     byName('维生素B2片').stockBase);
  ck('加 7 天：葡萄糖酸钙 34', byName('葡萄糖酸钙片').stockBase === 34,
     byName('葡萄糖酸钙片').stockBase);
  ck('加 7 天：维生素B12 1.5', byName('维生素B12注射液').stockBase === 1.5,
     byName('维生素B12注射液').stockBase);
  ck('加 7 天：没设用法的那味没被动（仍 30）', byName('维生素E软胶囊').stockBase === 30,
     byName('维生素E软胶囊').stockBase);
  ck('基准日滚到今天', byName('维生素C片').stockDate === todayStr(),
     byName('维生素C片').stockDate);
  ck('加完弹窗已关闭', !q('#dlgBulk').open, '');
  ck('提示说清了改了几味、改了多少天',
     /已把 5 种药的存量增加 7 天的量/.test(R.alerts.slice(alertsBefore).join(' | ')),
     R.alerts[R.alerts.length - 1]);

  // --- 7d. 减少到负数 → 截到 0，不许出现负库存 ---
  // 注：默认只勾「已用完 / 偏少」，这里要验全药箱一起减，所以先点「全选」。
  openBulk(); q('#bulkDays').value = 100; renderBulk();
  q('#btnBulkAll').click();
  ck('点全选后 5 味都在勾上（含充足的那味）', /已勾选 5 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  applyBulk(-1);
  var five = ['维生素C片','维生素B2片','葡萄糖酸钙片','健胃消食片','维生素B12注射液'];
  ck('减 100 天：5 味全到 0，没有负数',
     five.every(function(n){ return byName(n).stockBase === 0; }),
     five.map(function(n){ return byName(n).stockBase; }).join('/'));
  ck('减到 0 的药 level 是「已用完」', byName('健胃消食片') && level(byName('健胃消食片')) === 'out',
     level(byName('健胃消食片')));
  ck('没设用法的那味仍是 30', byName('维生素E软胶囊').stockBase === 30,
     byName('维生素E软胶囊').stockBase);
  ck('提示里说明了被减到 0 的味数', /已减到 0/.test(R.alerts[R.alerts.length - 1]),
     R.alerts[R.alerts.length - 1]);

  // --- 7e. 「增加」必须和单药「又买了药」同一口径（stockNow 基础上加） ---
  ck('批量加完后 stockNow == stockBase（基准日=今天，没被重复扣）',
     byName('维生素E软胶囊').stockBase === stockNow(byName('维生素E软胶囊')), '');

  // --- 7f. ★ 2026-10-12 新增：只勾一部分 → 没勾的必须一根手指都不碰 ---
  // 此刻 5 味都已被减到 0，维生素E 仍是 30。
  // 此刻 5 味都已用完 → 默认就把这 5 味全勾上了（「已用完」在默认档里），先把别的取消
  openBulk();
  ['维生素C片','维生素B2片','葡萄糖酸钙片','维生素B12注射液']
    .forEach(function(n){
      var c = document.querySelector('#bulkList input.bulk-ck[data-id="' + byName(n).id + '"]');
      c.checked = false;
      c.dispatchEvent(new Event('change', {bubbles:true}));
    });
  ck('只留健胃消食勾选（已勾选 1 / 5）', /已勾选 1 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  var alerts2 = R.alerts.length;
  applyBulk(1);
  ck('只勾一种 → 它确实变了（0 → 7）', byName('健胃消食片').stockBase === 7,
     byName('健胃消食片').stockBase);
  ck('没勾的没被动（维生素C仍 0）', byName('维生素C片').stockBase === 0,
     byName('维生素C片').stockBase);
  ck('没勾的没被动（葡萄糖酸钙仍 0）', byName('葡萄糖酸钙片').stockBase === 0,
     byName('葡萄糖酸钙片').stockBase);
  ck('没设用法的没被动（维生素E 仍 30）', byName('维生素E软胶囊').stockBase === 30,
     byName('维生素E软胶囊').stockBase);
  ck('提示只说改了 1 种', /已把 1 种药的存量增加 7 天的量/.test(R.alerts.slice(alerts2).join(' | ')),
     R.alerts[R.alerts.length - 1]);

  // 一种都不勾就点确认 → 拦下，且不改任何数据（按钮虽已禁用，这里直接调函数兜底）
  openBulk();
  q('#btnBulkNone').click();
  var alerts3 = R.alerts.length;
  applyBulk(1);
  ck('一种都没勾时点确认被拦下并提示', /还没勾选任何药/.test(R.alerts.slice(alerts3).join(' | ')),
     R.alerts[R.alerts.length - 1]);
  ck('被拦下时数据没动（健胃消食仍 7）', byName('健胃消食片').stockBase === 7,
     byName('健胃消食片').stockBase);
  q('#dlgBulk').close();

  // ---- 8. ★ 2026-10-11 用户两次报 bug 后的定稿口径 ----
  // 用户原话：「卡片上显示的 每日2片 余量约4片 这个4片就是正确的，你把卡片上的数和编辑里的数同步」。
  // 也就是说：**卡片上的数（stockNow）是唯一口径**，编辑弹窗要预填它、存它也存它。
  // 弹窗里那行蓝字「扣到今天：还剩 X 片 … 」**已按用户要求删掉**（2026-10-11 下半场）——
  //   余量那格预填的本来就是卡片上的数，再摆一行算式是冗余。断言改成"这行必须不存在"。
  var demo3 = [
    { name:'编弹窗对账片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13) },                        // 扣到今天 = 1 片 / 剩 1 天
    { name:'每日两片对账', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:4, stockDate:todayStr() },                       // 用户举的例子：每日 2 片、4 片 = 2 天
    { name:'三次两片对账', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:3,
      stockBase:12, stockDate:todayStr() },                      // 每次 2 片×每天 3 次 → 12 片 = 2 天
    { name:'零头对账片', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:5, stockDate:todayStr() },                       // 5 片 ÷ 每天 2 片 → 2 天多一点
    { name:'半天对账片', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:1, stockDate:todayStr() },                       // 1 片 < 每天 2 片 → 不够 1 整天
    { name:'没设用法片', spec:'—', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
      stockBase:30, stockDate:todayStr() }                       // 没设用法 → 不递减
  ];
  demo3.forEach(function(d){ d.id = uid(); });
  setMeds(demo3); save(); render();

  function mday(name){
    var c = card(name);
    return c ? c.querySelector('.days .v').textContent.trim() : '(无卡片)';
  }
  function mqty(name){
    var c = card(name);
    if(!c) return '(无卡片)';
    var mt = c.querySelector('.med-meta').textContent.replace(/\s+/g, ' ');
    var mm = mt.match(/余量约\s*([\d.]+)/);
    return mm ? mm[1] : '(没匹配到)';
  }
  function dlgNumTxt(sel){
    var el = q(sel);
    return el ? el.textContent.replace(/\s+/g, ' ').trim() : '(没找到 ' + sel + ')';
  }
  ck('编辑弹窗里已无那行蓝字（#stockPreview 已删）', !q('#stockPreview'), '');
  ck('产出那个蓝字的函数也已删掉', typeof syncStockPreview === 'undefined',
     typeof syncStockPreview);

  ck('卡片 对账片 = 1 片 / 1 天（14 片扣 13 天）',
     mqty('编弹窗对账片') === '1' && mday('编弹窗对账片') === '1',
     mqty('编弹窗对账片') + ' / ' + mday('编弹窗对账片'));
  ck('卡片 每日 2 片 · 余量约 4 片 · 2 天（用户说的那个例子）',
     mqty('每日两片对账') === '4' && mday('每日两片对账') === '2',
     mqty('每日两片对账') + ' / ' + mday('每日两片对账'));
  ck('卡片 每次 2 片 × 每天 3 次 · 12 片 · 2 天',
     mqty('三次两片对账') === '12' && mday('三次两片对账') === '2',
     mqty('三次两片对账') + ' / ' + mday('三次两片对账'));
  ck('卡片 5 片 ÷ 每天 2 片 → 2 天（余量如实写 5，不取整）',
     mqty('零头对账片') === '5' && mday('零头对账片') === '2',
     mqty('零头对账片') + ' / ' + mday('零头对账片'));
  ck('卡片 1 片 < 每天 2 片 → 天数写「不足 1」',
     mday('半天对账片') === '不足 1' && mqty('半天对账片') === '1',
     mday('半天对账片') + ' / ' + mqty('半天对账片'));

  // --- 8a. 弹窗「余量」预填 = 卡片上的数（同一个口径，不再有那行蓝字）---
  openEditor(byName('每日两片对账').id);
  var fmed = q('#formMed');
  ck('每日 2 片 · 弹窗「余量」预填 = 卡片上的 4',
     Math.abs(num(fmed.stockBase.value) - 4) < 1e-9, fmed.stockBase.value);
  ck('预填那个数下面不再有蓝字提示行', !q('#stockPreview'), '');

  openEditor(byName('三次两片对账').id);
  ck('每次 2 片×每天 3 次：弹窗预填 12 = 卡片 12',
     Math.abs(num(fmed.stockBase.value) - 12) < 1e-9, fmed.stockBase.value);
  q('#dlgMed').close();

  // --- 8b. 只改别的项 → 原来的基准（量 + 那天）原样保留，不许被预填值顶掉 ---
  openEditor(byName('编弹窗对账片').id);
  ck('弹窗预填的是扣到今天的 1 片（不是当初的基准 14）',
     Math.abs(num(fmed.stockBase.value) - 1) < 1e-9, fmed.stockBase.value);
  fmed.note.value = '只改备注';
  submitMed({ preventDefault: function(){} });
  var keep = byName('编弹窗对账片');
  ck('只改备注 → 基准仍是 14 片 / 13 天前（没被预填值顶掉）',
     keep.stockBase === 14 && keep.stockDate === agoD(13),
     keep.stockBase + ' / ' + keep.stockDate);

  // --- 8c. 真改了余量 → 存你填的数，基准日滚到今天 ---
  openEditor(byName('编弹窗对账片').id);
  fmed.stockBase.value = 4;
  submitMed({ preventDefault: function(){} });
  keep = byName('编弹窗对账片');
  ck('改了余量 → 存 4 片 / 基准今天',
     keep.stockBase === 4 && keep.stockDate === todayStr(),
     keep.stockBase + ' / ' + keep.stockDate);
  ck('改完卡片同步成 4 片 / 4 天',
     mqty('编弹窗对账片') === '4' && mday('编弹窗对账片') === '4',
     mqty('编弹窗对账片') + ' / ' + mday('编弹窗对账片'));

  // --- 8d. 除不尽 / 不足一天：预填的仍是卡片上那个精确数（不取整）---
  openEditor(byName('零头对账片').id);
  ck('除不尽（5 片 ÷ 每天 2 片）：预填仍是卡片的 5',
     Math.abs(num(fmed.stockBase.value) - 5) < 1e-9, fmed.stockBase.value);
  openEditor(byName('半天对账片').id);
  ck('不足一天（1 片 ÷ 每天 2 片）：预填仍是卡片的 1',
     Math.abs(num(fmed.stockBase.value) - 1) < 1e-9, fmed.stockBase.value);
  openEditor(byName('没设用法片').id);
  ck('没设用法：预填原数 30（不会被扣）',
     Math.abs(num(fmed.stockBase.value) - 30) < 1e-9, fmed.stockBase.value);
  // 新增（还没保存过）的药：余量预填 0，不许出现负数
  openEditor(null);
  ck('新增药品时余量预填 0', num(fmed.stockBase.value) === 0, fmed.stockBase.value);
  q('#dlgMed').close();

  // ---- 9. ★ 2026-10-11 用户要求：「又买了药」「校准余量」也走中间弹窗 ----
  //    原来是浏览器原生 prompt（顶部小框、手机上难用）。现在共用 #dlgNum，
  //    必须像「批量调存量」一样在页面中间，并且**不许再调 window.prompt**。
  //    ★ 2026-10-12 用户要求（两处）：①弹窗里那行实时对照整行删掉
  //      ——「现在推算还剩 X」/「系统推算 X → 校准为 Y」，元素 #numCalc 与函数
  //      renderNumCalc 都要消失；②输入单位不再用 0.25，改成 1 片一档。
  var restName = '每日两片对账';                  // 现在 4 片，每日 2 片
  setMeds([
    { name: restName, spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:4, stockDate:todayStr() },
    { name:'校准用对照片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:7, stockDate:todayStr() }
  ]);
  meds.forEach(function(d){ if(!d.id) d.id = uid(); });
  save(); render();

  // 9a. 又买了药
  var promptsBefore = R.prompts.length;
  restockMed(byName(restName).id);
  ck('「又买了药」打开的是中间弹窗 #dlgNum', q('#dlgNum') && q('#dlgNum').open, '');
  ck('没有调用浏览器原生 prompt', R.prompts.length === promptsBefore, R.prompts.join(' | '));
  ck('弹窗标题是「又买了药」', dlgNumTxt('#numTitle') === '又买了药', dlgNumTxt('#numTitle'));
  ck('输入行的标签写了单位', /又买了多少\s*片/.test(dlgNumTxt('#numLabel')), dlgNumTxt('#numLabel'));
  // ★ 那行实时对照必须**彻底消失**：元素不在 DOM、函数也不存在
  ck('#numCalc 那行实时对照已从 DOM 删掉', !q('#numCalc'), q('#numCalc') ? '还在' : '(无)');
  ck('renderNumCalc 函数也已删掉', typeof renderNumCalc === 'undefined', typeof renderNumCalc);
  // ★ 单位：1 片一档，不再是 0.25
  ck('输入框 step = 1（不再 0.25 一档）', q('#numVal').step === '1', q('#numVal').step);
  ck('表单带 novalidate（校验不弹浏览器原生气泡）',
     q('#formNum').hasAttribute('novalidate'), q('#formNum').hasAttribute('novalidate'));
  q('#numVal').value = '28';
  ck('按钮文案是「加到库存」', dlgNumTxt('#btnNumOk') === '加到库存', dlgNumTxt('#btnNumOk'));
  submitNum({ preventDefault: function(){} });
  ck('确认后库存 = 4 + 28 = 32', byName(restName).stockBase === 32, byName(restName).stockBase);
  ck('确认后弹窗已关闭', !q('#dlgNum').open, '');
  ck('卡片跟着变成 32', mqty(restName) === '32', mqty(restName));

  // 9b. 校准余量：预填系统推算值，填 0 也允许
  restockMed(byName(restName).id);            // 先开一次再取消，确认不写数据
  q('#numVal').value = '999';
  ck('取消不会改数据（仍是 32）', byName(restName).stockBase === 32, byName(restName).stockBase);
  q('#dlgNum').close();

  calibMed(byName('校准用对照片').id);
  ck('「校准余量」用的也是 #dlgNum', q('#dlgNum').open, '');
  ck('校准标题正确', dlgNumTxt('#numTitle') === '校准余量', dlgNumTxt('#numTitle'));
  ck('校准预填系统推算的 7', q('#numVal').value === '7', q('#numVal').value);
  ck('校准弹窗里同样没有那行对照', !q('#numCalc'), q('#numCalc') ? '还在' : '(无)');
  ck('校准的提示写明按整数片填', /按整数\s*片/.test(dlgNumTxt('#numHint')), dlgNumTxt('#numHint'));
  ck('校准的输入框 step 也是 1', q('#numVal').step === '1', q('#numVal').step);
  q('#numVal').value = '3';
  submitNum({ preventDefault: function(){} });
  ck('校准后存 3 片 / 基准今天',
     byName('校准用对照片').stockBase === 3 && byName('校准用对照片').stockDate === todayStr(),
     byName('校准用对照片').stockBase + ' / ' + byName('校准用对照片').stockDate);
  ck('全程一次原生 prompt 都没调过', R.prompts.length === promptsBefore, R.prompts.join(' | '));

  // 9c. 边界：又买了药填 0 / 校准填负数 → 拦下且不改数据
  restockMed(byName(restName).id);
  q('#numVal').value = '0';
  submitNum({ preventDefault: function(){} });
  ck('又买了药填 0 被拦下（弹窗还开着）', q('#dlgNum').open, '');
  ck('被拦下时数据没动（仍 32）', byName(restName).stockBase === 32, byName(restName).stockBase);
  ck('给了提示', /大于 0/.test(R.alerts.join(' | ')), R.alerts[R.alerts.length - 1]);
  q('#dlgNum').close();

  // ---- 9. 页脚署名：网页最下面「一条横线 + by 人间小土鸡」（2026-10-12 用户要求）----
  var sig = document.querySelector('.foot-sig');
  ck('页面底部有署名容器 .foot-sig', !!sig, sig ? '有' : '(无)');
  ck('署名文案是「by 人间小土鸡」',
     sig ? sig.textContent.trim() === 'by 人间小土鸡' : false,
     sig ? sig.textContent.trim() : '(无)');
  ck('署名前面有一条可见的横线（border-top 且不是最淡的分隔色）',
     (function(){
       if(!sig) return false;
       var s = getComputedStyle(sig);
       var w = parseFloat(s.borderTopWidth);
       // var(--line)=#e6e8ec 是常规分隔色；var(--line-2)=#eef0f4 淡到几乎看不见
       var c = s.borderTopColor.replace(/\s/g, '');
       return w >= 1 && c !== 'rgba(0,0,0,0)' && c !== 'rgb(238,240,244)';
     })(), sig ? getComputedStyle(sig).borderTopWidth + ' / ' + getComputedStyle(sig).borderTopColor : '(无)');
  // 位置：必须在 .wrap 里、两栏之后（真的在「最下面」，不是塞在别处）
  ck('署名在 .wrap 内、两栏之后（位于页面内容末尾）',
     !!(sig && sig.closest('.wrap') && sig.previousElementSibling
        && sig.previousElementSibling.classList.contains('cols')),
     sig && sig.previousElementSibling ? sig.previousElementSibling.className : '(无前一个兄弟)');
  // 只有一处，别重复
  ck('署名只出现一次', document.querySelectorAll('.foot-sig').length === 1,
     document.querySelectorAll('.foot-sig').length);

  // ---- 10. ★ 2026-10-12 用户要求：拍底方批量录入 ----
  //   用户原话：「用户能直接拍照 来批量录入开过的药品 然后程序引导用户确认每种药都能用多少天
  //   …在我的药品里 现在只有添加第一种药的按钮 应该再加一个拍底方录入药品的按钮」。
  //   ★ 识别那一步交给助手（人来看照片），**网页里不做 OCR**：不引外部脚本、不下载模型，
  //     因为中文药品名认错一两个字是要吃错药的，而这是个单文件、离线可用的页面。
  //   ★ 下面的每一行都**照真底方的版式**写（药名(商品名)[厂家][规格] 数量 单价 用量 频次 用法），
  //     但药名一律是中性示例 —— 2026-10-12 用户要求「全换成中性药名」：真底方原文属于
  //     「已经输入过的信息」，不该跟着开源代码分发（他 2026-09-28 对内置示例数据就是这条要求）。
  //     版式特征一个没丢：圆括号罗马数字、方括号商品名/厂家、`*每盒数`、瓶/盒/支、
  //     用量÷规格 = 每次几片、每日 N 次 / 每两周 / 每晚 / 外用 / 皮下注射。
  //     本机另有一份**不进仓库**的真药名名单（`_check/_tmp_deny_names.txt`，被 .gitignore 挡着），
  //     `main()` 拿它扫 index.html —— 以后谁再顺手粘一段真底方进来，断言当场红。

  // 10a. 两个入口
  ck('顶部工具栏有「拍底方录入」按钮', !!q('#btnRx'), '');
  setMeds([]); save(); render();
  ck('空药箱里也加了「拍底方批量录入」（和「添加第一种药」并排）',
     !!(q('#btnRx2') && q('#btnAdd2')), '');
  setMeds([{ id: uid(), name:'占位药', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
             stockBase:30, stockDate:todayStr() }]);
  save(); render();

  // 10b. 逐行解析：真底方原文 → 结构化字段
  var a1 = rxParseLine('维生素B6片(Ⅰ)[华康][10mg*30]  1瓶  19.88  10mg  每日一次  口服');
  ck('维生素B6片：药名拆对（括号里的「华康」没混进名字）', a1.name === '维生素B6片', a1.name);
  ck('维生素B6片：规格 10mg*30、单位「片」', a1.spec === '10mg*30' && a1.unit === '片',
     a1.spec + ' / ' + a1.unit);
  ck('维生素B6片：1 瓶 × 30 片 → 本次 30 片', a1.total === 30, a1.total);
  ck('维生素B6片：每次 1 片 · 每日 1 次 → 够 30 天', a1.perDose === 1 && rxDaysOf(a1) === 30,
     a1.perDose + ' / ' + rxDaysOf(a1));

  var a2 = rxParseLine('阿莫西林胶囊(康贝)[0.25g*8]  2盒  6.26  0.5g  每日2次  口服');
  ck('阿莫西林：用量 0.5g ÷ 规格 0.25g → 每次 2 粒', a2.perDose === 2, a2.perDose);
  ck('阿莫西林：胶囊 → 单位自动取「粒」', a2.unit === '粒', a2.unit);
  ck('阿莫西林：2 盒 × 8 粒 → 16 粒，够 4 天', a2.total === 16 && rxDaysOf(a2) === 4,
     a2.total + ' / ' + rxDaysOf(a2));

  var a3 = rxParseLine('维生素B12注射液[甲钴胺][1ml:0.5mg*]  1盒  15.60  0.5mg  每两周一次  皮下注射');
  ck('维生素B12：「每两周一次」→ 每 14 天 1 次', a3.cycleDays === 14 && a3.timesPerCycle === 1,
     a3.cycleDays + ' / ' + a3.timesPerCycle);
  ck('维生素B12：注射液 → 单位「支」，够 14 天', a3.unit === '支' && rxDaysOf(a3) === 14,
     a3.unit + ' / ' + rxDaysOf(a3));
  ck('维生素B12：「皮下注射」进备注', a3.note === '皮下注射', a3.note);

  var a4 = rxParseLine('麝香壮骨膏[40mg*6贴]  5盒  57.87  1贴  每日一次  外用');
  ck('麝香壮骨：规格里的包装单位「贴」被认出来', a4.unit === '贴' && a4.total === 30,
     a4.unit + ' / ' + a4.total);
  ck('麝香壮骨：「外用」进备注', a4.note === '外用', a4.note);
  ck('口服的不写进备注（免得每张卡片都挂个「口服」）', a1.note === '', '「' + a1.note + '」');

  var a5 = rxParseLine('氯雷他定片[10mg*14]  1盒  6.58  10mg  每晚  口服');
  ck('「每晚」当每日 1 次 → 够 14 天', a5.cycleDays === 1 && rxDaysOf(a5) === 14, rxDaysOf(a5));

  var a6 = rxParseLine('阿奇霉素片[0.25g*60]  2盒  6.12  0.5g  每日2次  口服');
  ck('规格 0.25g + 用量 0.5g → 每次 2 片', a6.perDose === 2, a6.perDose);
  ck('2 盒 × 60 片 → 120 片，够 30 天', a6.total === 120 && rxDaysOf(a6) === 30,
     a6.total + ' / ' + rxDaysOf(a6));

  // 认不出来的字段：留默认值 + 明确提醒，绝不猜一个看起来很像的数
  var a7 = rxParseLine('阿莫西林胶囊 0.25g 2盒 0.5g 每日2次');
  ck('手打行也认得出（末尾那个 0.25g 挪去当规格，不进药名）',
     a7.name === '阿莫西林胶囊' && a7.spec === '0.25g', a7.name + ' / ' + a7.spec);
  ck('没认出每盒多少 → 明确提醒去核对，不瞎填', /没认出每盒多少/.test(a7.warn), a7.warn);

  // 10c. 整段处方粘进来：表头 / 金额 / 医师 / 打印日期这类行要跳过
  var bulk = rxParseText([
    '药品名称（规格）  数量  单价  用量  频次  用法  备注',
    '奥美拉唑肠溶胶囊[20mg*8]  1盒  28.50  20mg  每日一次  口服',
    '金额：913.65元',
    '医师：王小鸭',
    '叶酸片[4mg*30]  1盒  8.21  4mg  每日一次  口服',
    '',
    '打印日期：2026-10-12 10:43:57'
  ].join('\n'));
  ck('一段处方文字 → 只挑出真正的药（跳过表头/金额/医师/打印日期/空行）', bulk.length === 2, bulk.length);
  ck('挑出来的正是那两味药',
     bulk.length === 2 && bulk[0].name === '奥美拉唑肠溶胶囊' && bulk[1].name === '叶酸片',
     bulk.map(function(x){ return x.name; }).join(' / '));

  // 10d. 弹窗里走一遍：粘 → 解析 → 逐味确认天数 → 一次入库
  openRx();
  ck('刚打开时列表是空的、给了引导', /还没有药/.test(q('#rxList').textContent),
     q('#rxList').textContent.slice(0, 30));
  q('#rxPaste').value = '维生素B6片(Ⅰ)[华康][10mg*30] 1瓶 19.88 10mg 每日一次 口服\n'
                      + '阿莫西林胶囊(康贝)[0.25g*8] 2盒 6.26 0.5g 每日2次 口服';
  q('#btnRxParse').click();
  ck('解析后列出 2 行', document.querySelectorAll('#rxList .rx-row').length === 2,
     document.querySelectorAll('#rxList .rx-row').length);
  ck('每行当场给出「够吃几天」（30 / 4）',
     q('#rxList .rx-row:nth-child(1) .rx-days-in').value === '30'
     && q('#rxList .rx-row:nth-child(2) .rx-days-in').value === '4',
     q('#rxList .rx-row:nth-child(1) .rx-days-in').value + ' / '
     + q('#rxList .rx-row:nth-child(2) .rx-days-in').value);
  ck('解析完清空粘贴框（免得手一抖粘两遍）', q('#rxPaste').value === '', '「' + q('#rxPaste').value + '」');
  ck('汇总写清勾了几味、加入后各档位几味', /共 2 味，勾选 2 味/.test(q('#rxSum').textContent),
     q('#rxSum').textContent.slice(0, 44));
  ck('按钮写清要加几味', /把勾选的 2 味加入药箱/.test(q('#btnRxOk').textContent),
     q('#btnRxOk').textContent);

  // ★ 确认天数是**双向**的：改天数 → 本次开到跟着反算（用户定的口径「余量 = 天数 × 每天用量」）
  var d1 = q('#rxList .rx-row:nth-child(1) .rx-days-in');
  d1.value = '60';
  d1.dispatchEvent(new Event('input', {bubbles:true}));
  ck('把「够吃 30 天」改成 60 天 → 「本次开到」跟着变 60 片',
     q('#rxList .rx-row:nth-child(1) .rx-total').value === '60',
     q('#rxList .rx-row:nth-child(1) .rx-total').value);
  var t2 = q('#rxList .rx-row:nth-child(2) .rx-total');
  t2.value = '40';
  t2.dispatchEvent(new Event('input', {bubbles:true}));
  ck('把「16 粒」改成 40 粒 → 够吃天数变成 10 天',
     q('#rxList .rx-row:nth-child(2) .rx-days-in').value === '10',
     q('#rxList .rx-row:nth-child(2) .rx-days-in').value);
  ck('行色跟着档位变（40 粒 = 10 天 ≤ 偏少线 → soon）',
     q('#rxList .rx-row:nth-child(2)').getAttribute('data-lv') === 'soon',
     q('#rxList .rx-row:nth-child(2)').getAttribute('data-lv'));
  var sel2 = q('#rxList .rx-row:nth-child(2) .rx-freq');
  sel2.value = '1_3';
  sel2.dispatchEvent(new Event('change', {bubbles:true}));
  ck('频次下拉改成「每日 3 次」→ 天数当场重算成 6 天',
     q('#rxList .rx-row:nth-child(2) .rx-days-in').value === '6',
     q('#rxList .rx-row:nth-child(2) .rx-days-in').value);

  // 取消勾选一味 → 汇总/按钮跟着变，入库时那味一根手指都不碰
  var ck2 = q('#rxList .rx-row:nth-child(2) .rx-ck');
  ck2.checked = false;
  ck2.dispatchEvent(new Event('change', {bubbles:true}));
  ck('取消勾选 → 汇总变成「勾选 1 味」', /勾选 1 味/.test(q('#rxSum').textContent),
     q('#rxSum').textContent.slice(0, 30));
  ck('取消勾选 → 按钮也变成 1 味', /把勾选的 1 味加入药箱/.test(q('#btnRxOk').textContent),
     q('#btnRxOk').textContent);

  var before10 = meds.length;
  submitRx({ preventDefault: function(){} });
  ck('提交后药箱只多了 1 条（没勾的那味没进来）', meds.length === before10 + 1, meds.length - before10);
  var nm1 = byName('维生素B6片');
  ck('入库各项写对（余量 60 / 基准今天 / 每次 1 片 / 每日 1 次）',
     !!nm1 && nm1.stockBase === 60 && nm1.stockDate === todayStr()
     && nm1.perDose === 1 && nm1.timesPerCycle === 1 && nm1.cycleDays === 1,
     nm1 ? [nm1.stockBase, nm1.stockDate, nm1.perDose, nm1.timesPerCycle, nm1.cycleDays].join(' / ') : '(没进来)');
  ck('卡片上的天数 = 弹窗里确认过的天数（60 天）', mday('维生素B6片') === '60', mday('维生素B6片'));
  ck('没勾的那味确实没被加进来',
     !meds.some(function(x){ return x.name === '阿莫西林胶囊'; }), '');

  // 10e. 「把照片发给助手」那条路：带 #rx= 的链接打开就把药摆好
  var enc = rxEncode([{ name:'维生素B12注射液', spec:'1ml:0.5mg*', unit:'支', perDose:1,
                        cycleDays:14, timesPerCycle:1, total:1 }]);
  ck('rxEncode 产出的是 URL 安全字符（能直接贴进聊天发出去）',
     /^[A-Za-z0-9\-_]+$/.test(enc) && enc.length > 20, enc.slice(0, 20) + '…');
  var back = JSON.parse(decodeURIComponent(escape(
    atob(enc.replace(/-/g, '+').replace(/_/g, '/')))));
  ck('这段编码解得回原来那味药',
     back.rows.length === 1 && back.rows[0].name === '维生素B12注射液', back.rows[0].name);
  openRx(back.rows, '助手');
  ck('预填的弹窗里药已经摆好、天数也算好了',
     document.querySelectorAll('#rxList .rx-row').length === 1
     && q('#rxList .rx-row:nth-child(1) .rx-days-in').value === '14',
     document.querySelectorAll('#rxList .rx-row').length + ' 行');
  ck('说明里写清「助手已经认好了，你核对一下」',
     /已经从你的底方照片上认好/.test(q('#rxSub').textContent), q('#rxSub').textContent.slice(0, 36));
  q('#dlgRx').close();

  // 名称空着的行：拦下，不许静默入库
  openRx([{ name:'', unit:'片', perDose:1, cycleDays:1, timesPerCycle:1, total:10 }]);
  var n0 = meds.length;
  submitRx({ preventDefault: function(){} });
  ck('名称空着的那味被拦下（没入库）', meds.length === n0, meds.length - n0);
  ck('并且给了提示', /还没填药品名称/.test(R.alerts[R.alerts.length - 1]),
     R.alerts[R.alerts.length - 1]);
  q('#dlgRx').close();

  // ============ 11. ★ 2026-10-13 用户要求：照片自动转正 + 浏览器自动 OCR + 批量填用法 ============
  //   用户原话：「拍底方录入这里 既有用你来做接口的 也包括浏览器自动ocr的吧 冗余一下。
  //   现在的问题是 照片发进去 解析不出来。我认为应该首先 程序能自动旋转图片 让文字方向能正确；
  //   第二 能识别出其中的药名。至于每种药的具体吃法 能吃多久，既可以通过你作为接口通过对话的
  //   方式来录入，也可以在浏览器里在拍底方之后 再跳一个窗口 批量的人手工来输入信息。」
  //   → 分四块验：① 方向换算（纯函数 + 真转一张图按像素验）；② EXIF 读取；
  //     ③ 打分/选方向；④ 端到端：假引擎 → 认药 → 跳「批量填用法」→ 统一设置 → 入库 → 失败兜底。
  //   ★ 真引擎（tesseract + chi_sim，几十兆）没法在无头里下，所以这里塞 window.__rxOcrEngine
  //     当假引擎 —— **验的是流程和口径，不是识别率**。

  setMeds([]); save(); render();

  // ---- 11a. 方向换算 ----
  ck('EXIF 1 = 不用转', rxOrientSpec(1).deg === 0 && rxOrientSpec(1).mirror === false, '');
  ck('EXIF 3 = 转 180°', rxOrientSpec(3).deg === 180, rxOrientSpec(3).deg);
  ck('EXIF 6 = 顺时针 90°（手机竖着拍最常见的那种）',
     rxOrientSpec(6).deg === 90 && rxOrientSpec(6).mirror === false, rxOrientSpec(6).deg);
  ck('EXIF 8 = 顺时针 270°', rxOrientSpec(8).deg === 270, rxOrientSpec(8).deg);
  ck('EXIF 5 = 先镜像再转 270°',
     rxOrientSpec(5).deg === 270 && rxOrientSpec(5).mirror === true, '');
  ck('EXIF 读到乱七八糟的值 → 退回「不用转」，不瞎转',
     rxOrientSpec(99).deg === 0 && rxOrientSpec(0).deg === 0, '');
  ck('转 90° 时宽高互换',
     rxOrientSize(90, 4000, 3000).w === 3000 && rxOrientSize(90, 4000, 3000).h === 4000, '');
  ck('转 180° 时宽高不变', rxOrientSize(180, 4000, 3000).w === 4000, '');

  // 真转一张图，按像素验：4×2 八格八色，EXIF=6 应该转成 2×4、原左上角跑到右上角
  var S = document.createElement('canvas'); S.width = 4; S.height = 2;
  var sctx = S.getContext('2d');
  var PX = ['#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ff00ff', '#00ffff', '#101010', '#808080'];
  for(var py = 0; py < 2; py++){
    for(var px = 0; px < 4; px++){
      sctx.fillStyle = PX[py * 4 + px]; sctx.fillRect(px, py, 1, 1);
    }
  }
  var pixAt = function(cv, x, y){
    var d = cv.getContext('2d').getImageData(x, y, 1, 1).data;
    return '#' + [d[0], d[1], d[2]].map(function(v){ return ('0' + v.toString(16)).slice(-2); }).join('');
  };
  var R6 = rxCanvasFrom(S, rxOrientSpec(6), 0, 99999);
  ck('EXIF 6 转出来宽高互换（4×2 → 2×4）', R6.width === 2 && R6.height === 4,
     R6.width + '×' + R6.height);
  ck('EXIF 6 转完，原左上角那格跑到右上角（顺时针 90°）',
     pixAt(R6, 1, 0) === PX[0], pixAt(R6, 1, 0) + ' 应该是 ' + PX[0]);
  ck('EXIF 6 转完，原左下角那格跑到左上角',
     pixAt(R6, 0, 0) === PX[4], pixAt(R6, 0, 0) + ' 应该是 ' + PX[4]);
  var R0 = rxCanvasFrom(S, rxOrientSpec(1), 0, 99999);
  ck('EXIF 1 一格都不动：尺寸和左上角都原样',
     R0.width === 4 && R0.height === 2 && pixAt(R0, 0, 0) === PX[0], R0.width + '×' + R0.height);
  var R2 = rxCanvasFrom(S, rxOrientSpec(2), 0, 99999);
  ck('EXIF 2 = 左右镜像：左上角那格跑到右上角，尺寸不变',
     R2.width === 4 && R2.height === 2 && pixAt(R2, 3, 0) === PX[0], pixAt(R2, 3, 0));
  ck('不用转时原样返回，不多拷一份（rxRotateCanvas(c,0) 就是它自己）', rxRotateCanvas(S, 0) === S, '');
  var RT90 = rxRotateCanvas(S, 90);
  ck('rxRotateCanvas 手动转 90° 也是宽高互换 + 左上角到右上角',
     RT90.width === 2 && RT90.height === 4 && pixAt(RT90, 1, 0) === PX[0],
     RT90.width + '×' + RT90.height);
  ck('手动 ↺ 再 ↻ 能转回原样（角度是累加的、不是覆盖的）',
     rxRotateCanvas(rxRotateCanvas(S, 90), 270).width === 4, '');
  var PRE = rxOcrPrep(S, 99999);
  ck('送识别前会灰化（三通道相等）',
     (function(){ var d = PRE.getContext('2d').getImageData(0, 0, 1, 1).data;
                  return d[0] === d[1] && d[1] === d[2]; })(), '');
  // ★ 上限要拿一张「真的超大」的图去试：拿 4×2 的小图配 cap=2，撞上 rxOcrPrep 里
  //   Math.max(320,…) 那条地板线，测的就不是「会不会缩」而是「地板线是多少」了。
  var BIG = document.createElement('canvas'); BIG.width = 4000; BIG.height = 2000;
  ck('送识别前会按上限把长边缩下来（缩了才不白等）',
     rxOcrPrep(BIG, 1000).width === 1000 && rxOcrPrep(BIG, 1000).height === 500,
     rxOcrPrep(BIG, 1000).width + '×' + rxOcrPrep(BIG, 1000).height);
  ck('本来就没超上限就不放大（放大只会更慢）', rxOcrPrep(S, 99999).width === 4, rxOcrPrep(S, 99999).width);

  // ---- 11b. EXIF 读取（造一段最小可用的 JPEG 字节：SOI + APP1(Exif) + IFD0 里一条 Orientation）----
  var makeJpeg = function(orientation){
    var b = [0xFF, 0xD8,                          // SOI
             0xFF, 0xE1, 0x00, 0x22,              // APP1，长度 34
             0x45, 0x78, 0x69, 0x66, 0x00, 0x00, // "Exif\0\0"
             0x49, 0x49, 0x2A, 0x00, 0x08, 0x00, 0x00, 0x00,  // TIFF 头（小端，IFD0 在 +8）
             0x01, 0x00,                          // IFD0 条目数 = 1
             0x12, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00,  // tag = 0x0112(Orientation) SHORT
             orientation, 0x00, 0x00, 0x00,       // 值（小端）
             0x00, 0x00, 0x00, 0x00,              // 没有下一个 IFD
             0xFF, 0xD9];                         // EOI
    return new Uint8Array(b).buffer;
  };
  ck('从 JPEG 字节里读出 Orientation = 6', rxExifOrient(makeJpeg(6)) === 6, rxExifOrient(makeJpeg(6)));
  ck('Orientation = 8 也读得对', rxExifOrient(makeJpeg(8)) === 8, rxExifOrient(makeJpeg(8)));
  ck('不是 JPEG（PNG 头）→ 当成「不用转」，不乱猜',
     rxExifOrient(new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]).buffer) === 1, '');
  ck('空 / 残缺的字节不炸（读不出来就当不用转）',
     rxExifOrient(new ArrayBuffer(2)) === 1 && rxExifOrient(new ArrayBuffer(0)) === 1, '');

  // ---- 11c. 打分 / 选方向（只用来挑方向，不参与任何入库判断）----
  var scoreGood = rxOcrScore({ text: '阿莫西林胶囊 2盒 每日2次\n叶酸片 1盒 每日一次', confidence: 82 });
  var scoreJunk = rxOcrScore({ text: '|||| ||| ‖', confidence: 12 });
  ck('认出一堆药名的分数远高于一堆符号',
     scoreGood > scoreJunk * 3, scoreGood.toFixed(0) + ' vs ' + scoreJunk.toFixed(0));
  ck('空结果就是 0 分', rxOcrScore({ text: '', confidence: 0 }) === 0, rxOcrScore({ text: '', confidence: 0 }));
  ck('选方向：认得最多的那个赢',
     rxBestTurn([{ turn: 0, score: 12 }, { turn: 90, score: 210 },
                 { turn: 180, score: 8 }, { turn: 270, score: 3 }]).turn === 90, '');
  ck('方向打平 → 取转得少的那个（少动比多动稳）',
     rxBestTurn([{ turn: 180, score: 50 }, { turn: 90, score: 50 }]).turn === 90, '');
  ck('一个都没有 → null', rxBestTurn([]) === null, '');

  // ---- 11d. 端到端：假引擎跑完整链路（程序不联网也能测）----
  var rxSrc = document.createElement('canvas');   // 假装它是那张底方照片
  rxSrc.width = 600; rxSrc.height = 800;
  rxSrc.getContext('2d').fillRect(0, 0, 600, 800);

  var OCR_LINES = '维生素B6片(Ⅰ)[华康][10mg*30]  1瓶  19.88  10mg  每日一次  口服\n'
                + '阿莫西林胶囊(康贝)[0.25g*8]  2盒  6.26  0.5g  每日2次  口服\n'
                + '麝香壮骨膏[40mg*6贴]  5盒  57.87  1贴  每日一次  外用\n'
                + '金额：913.65元\n';
  var engineCalls = [];
  window.__rxOcrEngine = function(cv){
    engineCalls.push(cv.width + 'x' + cv.height);
    return Promise.resolve({ text: OCR_LINES, confidence: 88 });
  };

  openRx();
  ck('还没照片时，「自动识别药名」是禁用的（点了也没用的事就别让点）',
     q('#btnRxOcr').disabled === true, q('#btnRxOcr').disabled);
  rxPhotoBase = rxSrc; rxTurn = 0; rxShowPhotoView();
  ck('放了照片后按钮可用，缩略图里是画布（不是外链图片）',
     q('#btnRxOcr').disabled === false && !!q('#rxThumb canvas'), '');
  q('#btnRxRotR').click();
  ck('手动 ↻ 转一下角度 +90°', rxTurn === 90, rxTurn);
  q('#btnRxRotL').click();
  ck('手动 ↺ 转回来角度归零', rxTurn === 0, rxTurn);

  async function afterOcr(){
    ck('端到端识别跑完了', true, '');
    ck('方向本来就对 → 只跑一遍识别（没白试另外三个方向）', engineCalls.length === 1, engineCalls.length);
    ck('从照片上一口认出 3 味药（「金额」那行被跳过）', rxRows.length === 3,
       rxRows.map(function(x){ return x.name; }).join(' / '));
    ck('认出来的正是那三味',
       rxRows.map(function(x){ return x.name; }).join('/') === '维生素B6片/阿莫西林胶囊/麝香壮骨膏', '');
    ck('阿莫西林：用量 0.5g ÷ 规格 0.25g → 每次 2 粒（识别链路和粘贴链路同一套解析）',
       (function(){ var r = rxRows[1]; return r && r.perDose === 2 && r.unit === '粒'; })(),
       (function(){ var r = rxRows[1]; return r ? r.perDose + '/' + r.unit : '(没认出来)'; })());
    ck('自动跳出了「批量填用法」窗口', q('#dlgUse').open === true, q('#dlgUse').open);
    ck('窗口里逐味列出 3 行',
       document.querySelectorAll('#useList .use-row').length === 3,
       document.querySelectorAll('#useList .use-row').length);
    ck('识别原文留在粘贴框里，方便自己改',
       /维生素B6片/.test(q('#rxPaste').value), q('#rxPaste').value.slice(0, 20));
    ck('粘贴框的说明也改口成「识别原文，可以改」',
       /识别原文/.test(q('#rxPasteLt').textContent), q('#rxPasteLt').textContent.slice(0, 22));
    ck('「复制识别文字」出现了（可以甩给助手核对）', q('#btnRxCopyText').hidden === false, '');
    ck('提示里写明「药名务必核对，认错要吃错药」',
       /吃错药/.test(q('#rxOcrMsg').textContent), q('#rxOcrMsg').textContent.slice(0, 44));
    ck('识别完进度条收起、按钮回到正常文案',
       q('#rxOcrBar').classList.contains('on') === false && q('#btnRxOcr').textContent === '自动识别药名',
       q('#btnRxOcr').textContent);

    // 统一设置：每次 1 片 · 每日 2 次 · 开 10 天 → 一次套到全部
    q('#usePd').value = '1';
    q('#useFreq').value = '1_2';
    q('#useDays').value = '10';
    q('#btnUseApplyAll').click();
    ck('「应用到全部」把 3 味的用法都改成了 每日 2 次',
       rxRows.every(function(r){ return r.perDose === 1 && r.timesPerCycle === 2 && r.cycleDays === 1; }),
       rxRows.map(function(r){ return r.perDose + '×' + r.timesPerCycle; }).join(' / '));
    ck('「本次开到」跟着反算：每天 2 份 × 10 天 = 20 份',
       rxRows.every(function(r){ return r.total === 20; }),
       rxRows.map(function(r){ return r.total; }).join(' / '));
    ck('套完给了回执（不是默默改完让人不知情）',
       /套到全部 3 味/.test(q('#useNote').textContent), q('#useNote').textContent);
    ck('「本次开到」那格当场跟着变',
       q('#useList .use-row:nth-child(1) .use-tot').textContent === '20',
       q('#useList .use-row:nth-child(1) .use-tot').textContent);

    // 逐味微调：第 2 味改成每次 2 粒 → 总量不动、天数重算（和上一页行内编辑同一套规则）
    var p2 = q('#useList .use-row:nth-child(2) .use-pd');
    p2.value = '2';
    p2.dispatchEvent(new Event('input', { bubbles: true }));
    ck('改「每次 2 粒」→ 天数重算成 5 天（20 份 ÷ 每天 4 粒）',
       q('#useList .use-row:nth-child(2) .use-days').value === '5',
       q('#useList .use-row:nth-child(2) .use-days').value);
    var d2 = q('#useList .use-row:nth-child(2) .use-days');
    d2.value = '3';
    d2.dispatchEvent(new Event('input', { bubbles: true }));
    ck('再改「开 3 天」→ 本次开到反算成 12 粒',
       q('#useList .use-row:nth-child(2) .use-tot').textContent === '12',
       q('#useList .use-row:nth-child(2) .use-tot').textContent);
    ck('底部汇总跟着变（合计 20+12+20 = 52）',
       /52/.test(q('#useSum').textContent), q('#useSum').textContent.slice(0, 30));

    q('#dlgUse').close();
    // ★ dialog.close() 派发 close 事件是「排队」的（规范里 queue an element task），
    //   这一步同步返回时事件还没到 —— 不等一拍就断言，读到的是没重画的旧 DOM。
    await new Promise(function(res){ setTimeout(res, 0); });
    ck('关掉批量填用法窗口 → 上一页重画，天数跟着变',
       q('#rxList .rx-row:nth-child(2) .rx-days-in').value === '3',
       q('#rxList .rx-row:nth-child(2) .rx-days-in').value);

    var nb = meds.length;
    submitRx({ preventDefault: function(){} });
    ck('一次入库 3 味', meds.length === nb + 3, meds.length - nb);
    ck('维生素B6片入库：余量 20（每天 2 片 × 10 天）',
       (function(){ var m = byName('维生素B6片'); return !!m && m.stockBase === 20; })(),
       (function(){ var m = byName('维生素B6片'); return m ? m.stockBase : '(没进来)'; })());
    ck('卡片上的「剩 N 天」= 批量填用法里确认的天数（10 天）',
       mday('维生素B6片') === '10', mday('维生素B6片'));
    q('#dlgRx').close();

    // 失败兜底：引擎挂了不许静默，也不许把按钮卡在「识别中…」
    openRx();
    rxPhotoBase = rxSrc; rxTurn = 0; rxShowPhotoView();
    window.__rxOcrEngine = function(){ return Promise.reject(new Error('模拟断网')); };
    return rxRunOcr().then(function(){
      ck('识别失败时明说「这条路走不通」，并把人引回粘贴/助手那条路',
         /这条路走不通/.test(q('#rxOcrMsg').textContent) && /粘进下面的框/.test(q('#rxOcrMsg').textContent),
         q('#rxOcrMsg').textContent.slice(0, 48));
      ck('失败后按钮恢复可用（不会卡在「识别中…」）',
         q('#btnRxOcr').disabled === false && q('#btnRxOcr').textContent === '自动识别药名',
         q('#btnRxOcr').textContent);
      ck('失败后进度条收起', q('#rxOcrBar').classList.contains('on') === false, '');
      ck('失败不影响手动那条路：粘贴框照常解析',
         (function(){ q('#rxPaste').value = '叶酸片[4mg*30] 1盒 8.21 4mg 每日一次 口服';
                      q('#btnRxParse').click();
                      return rxRows.some(function(r){ return r.name === '叶酸片'; }); })(),
         rxRows.length);
      q('#dlgRx').close();
      delete window.__rxOcrEngine;
    });
  }

  function rxFinish(){
    R.meds = meds.map(function(x){ return x.name + '|qty=' + dispQty(x) + '|cap=' + rxCap(x); });
    var pre = document.createElement('pre');
    pre.id = '__T';
    pre.textContent = JSON.stringify(R);
    document.body.appendChild(pre);
  }

  // ★ 端到端那段要等 Promise（假引擎是异步的）。无头 Chrome 用 --virtual-time-budget，
  //   本地 Promise 在预算内就跑完了；万一哪天这里拿不到输出，就把这段改回同步。
  rxRunOcr().then(afterOcr).then(rxFinish, function(e){
    ck('端到端识别过程没抛错', false, String((e && e.message) || e));
    rxFinish();
  });
})();
"""


def main():
    src = open(SRC, encoding="utf-8").read()
    # ★ 开源前的要求：内置示例数据必须已抹掉（它带着症状描述等「已输入的信息」）
    for bad in ("function loadDemo", "先看示例数据", "?demo=1", "血压", "头晕"):
        if bad in src:
            print("!! 源码里仍有内置示例痕迹：%s" % bad)
            return 4
    print("  OK  源码已无内置示例数据（函数 / 按钮文案 / URL 参数 / 病情描述）")
    anchor = "})();\n</script>"
    if anchor not in src:
        print("找不到主 IIFE 结尾锚点")
        return 2
    # ★ 主脚本是 IIFE，meds / dispQty 等都在闭包里 —— 探针必须插在 IIFE 内部末尾，
    #   写成独立的 <script> 会直接 ReferenceError（第一次就栽在这）。
    i = src.rindex(anchor)
    page_src = src[:i] + PROBE + src[i:]
    tmpdir = tempfile.mkdtemp(prefix="dk_test_")
    page = os.path.join(tmpdir, "t.html")
    open(page, "w", encoding="utf-8").write(page_src)

    profile = os.path.join(tmpdir, "prof")
    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--virtual-time-budget=6000",
           "--user-data-dir=" + profile, "--dump-dom", url]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    dom = r.stdout.decode("utf-8", "replace")

    # ★ --dump-dom 会把 <script> 源码也吐出来，里面有 'JSON' / '单次限' 之类字面量，
    #   先剥掉再找结果，否则会误判（上次就栽在这）。
    dom_ns = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
    mm = re.search(r'<pre id="__T">(.*?)</pre>', dom_ns, flags=re.S)
    if not mm:
        print("!! 没拿到测试输出，DOM 长度", len(dom))
        print(dom_ns[:1500])
        return 3
    data = json.loads(html.unescape(mm.group(1)))

    # ---- 源码级：署名不进打印（打印出去的是给医生的药清单，署名属噪音）----
    mprint = re.search(r'@media\s+print\s*\{(.*?)\n\}', src, flags=re.S)
    data["cases"].append({
        "name": "署名已排除在打印之外（@media print 里隐藏 .foot-sig）",
        "pass": bool(mprint) and ".foot-sig" in mprint.group(1),
        "got": "已隐藏" if (mprint and ".foot-sig" in mprint.group(1)) else "(未隐藏)",
    })
    # ---- 源码级：署名唯一且文案正确 ----
    n_sig = src.count('class="foot-sig"')
    data["cases"].append({
        "name": "源码里署名容器只有一处",
        "pass": n_sig == 1,
        "got": n_sig,
    })
    # ---- 源码级：单数字弹窗那行实时对照已彻底清除（2026-10-12 用户要求）----
    #   只数「代码痕迹」，注释里提到这个名字不算（注释是留给维护者的说明）
    n_calc = (src.count('id="numCalc"') + src.count('renderNumCalc(')
              + src.count("#numCalc')"))
    data["cases"].append({
        "name": "源码里已无 numCalc 元素 / renderNumCalc 调用（注释除外）",
        "pass": n_calc == 0,
        "got": n_calc,
    })
    # ---- 源码级：单位不再是 0.25 一档（#numVal 的 step 已是 1）----
    mstep = re.search(r'id="numVal"[^>]*step="([^"]+)"', src)
    data["cases"].append({
        "name": "源码里 #numVal 的 step = 1（不再 0.25）",
        "pass": bool(mstep) and mstep.group(1) == "1",
        "got": mstep.group(1) if mstep else "(没找到)",
    })

    # ---- 源码级：拍底方这两条路（2026-10-13 用户要求「助手接口 + 浏览器自动 OCR，冗余一下」）----
    #   ★ 打开页面本身仍然不联网：静态标签里没有任何 src/href 指向外网。
    #   ★ OCR 引擎改成**点了才下载**（运行时 createElement('script') 塞进去，三个镜像兜底），
    #     所以「离线可用」这条没丢；取不到就明说并把人引回粘贴/助手那条路。
    n_net = len(re.findall(r'''(?:src|href)\s*=\s*["\']https?://''', src))
    data["cases"].append({
        "name": "静态标签里没有任何外网 src/href（打开页面本身不联网）",
        "pass": n_net == 0,
        "got": n_net,
    })
    data["cases"].append({
        "name": "OCR 引擎是运行时按需下载的（<script src=...> 这种常驻外链一个都没有）",
        "pass": ("<script src" not in src) and ("document.createElement('script')" in src),
        "got": "有 createElement('script')" if "document.createElement('script')" in src else "(没找到加载器)",
    })
    data["cases"].append({
        "name": "识别引擎有三个镜像兜底（一个站挂了还有下一个）",
        "pass": src.count("tesseract.js@5.1.1/dist/tesseract.min.js") >= 3,
        "got": src.count("tesseract.js@5.1.1/dist/tesseract.min.js"),
    })
    data["cases"].append({
        "name": "取不到识别引擎时不静默失败：明说「这条路走不通」，并把人引回粘贴/助手那条路",
        "pass": ("这条路走不通" in src) and ("粘进下面的框" in src),
        "got": "已写明" if (("这条路走不通" in src) and ("粘进下面的框" in src)) else "(没写)",
    })
    data["cases"].append({
        "name": "识别结果一律当草稿：界面上写明「药名务必核对，认错要吃错药」",
        "pass": "吃错药" in src,
        "got": "已写明" if "吃错药" in src else "(没写)",
    })
    data["cases"].append({
        "name": "照片说明里写明了「不上传、不保存」（识别在本机画布里跑）",
        "pass": ("不上传" in src) and ("不保存" in src),
        "got": "已写明" if (("不上传" in src) and ("不保存" in src)) else "(没写)",
    })
    data["cases"].append({
        "name": "两个入口都在：助手那条 #rx= 链接 + 网页自动识别（互为冗余）",
        "pass": ("rxDecodeHash" in src) and ("btnRxOcr" in src) and ("rxRunOcr" in src),
        "got": "都有" if (("rxDecodeHash" in src) and ("btnRxOcr" in src)) else "(缺一个)",
    })

    # ---- 源码级：拍底方这块**不做 OCR、不引外部依赖**（识别由助手代劳，2026-10-12 定）----
    # ★ 2026-10-12 用户要求「全换成中性药名」：真底方上的药名属于「已经输入过的信息」，
    #   不该跟着开源代码分发（他 2026-09-28 对内置示例数据就是这条要求）。
    #   名单**只在本机**（`_check/_tmp_deny_names.txt`，被 .gitignore 的 `_check/_tmp_*` 挡着）——
    #   把真药名写进断言本身就把它们发出去了，所以这里读外部文件，没有名单就跳过这条。
    here = os.path.dirname(os.path.abspath(__file__))
    deny = os.path.join(here, "_tmp_deny_names.txt")
    if os.path.exists(deny):
        with open(deny, encoding="utf-8") as fh:
            names = [ln.strip() for ln in fh
                     if ln.strip() and not ln.lstrip().startswith("#")]
        ships = [("index.html", src)]
        for fn in ("README.md", "_check/test_rxcap.py", "_check/shot.py"):
            p = os.path.join(os.path.dirname(here), fn)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    ships.append((fn, fh.read()))
        hits = []
        for nm in names:
            for fn, txt in ships:
                if nm in txt:
                    hits.append("%s→%s" % (nm, fn))
        data["cases"].append({
            "name": "要发布的文件里没有真底方上的药名（本机名单 %d 条）" % len(names),
            "pass": not hits,
            "got": "、".join(hits) if hits else "0",
        })

    fails = [c for c in data["cases"] if not c["pass"]]
    for c in data["cases"]:
        print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c["got"]))
    print("\n" + "-" * 60)
    print("药箱快照：")
    for line in data.get("meds", []):
        print("   " + line)
    print("-" * 60)
    print("通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
