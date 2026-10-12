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
    { name:'苯磺酸氨氯地平片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:7 },
    { name:'阿托伐他汀钙片', spec:'20mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:21, stockDate:agoD(18), rxMax:14 },
    { name:'盐酸二甲双胍缓释片', spec:'0.5g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
      stockBase:60, stockDate:agoD(20) },
    { name:'阿司匹林肠溶片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:90, stockDate:agoD(10) },
    { name:'依洛尤单抗注射液', spec:'140mg/支', perDose:1, unit:'支', cycleDays:14, timesPerCycle:1,
      stockBase:2, stockDate:agoD(20), rxMax:2 }
  ];
  demo.forEach(function(d){ d.id = uid(); });
  setMeds(demo);
  save(); render();
  ck('自注入 5 种药后渲染成功', meds.length === 5, meds.length);

  // ---- 1. 有单次上限、且一次开不完：入库量 = 上限，不是理论缺口 ----
  var a = byName('苯磺酸氨氯地平片');
  ck('氨氯地平 理论缺口=29', a && needUnits(a) === 29, a ? needUnits(a) : null);
  ck('氨氯地平 单次限=7', a && rxCap(a) === 7, a ? rxCap(a) : null);
  ck('氨氯地平 需补=7（跟单次限）', a && dispQty(a) === 7, a ? dispQty(a) : null);
  ck('氨氯地平 得开 5 次', a && rxTrips(a) === 5, a ? rxTrips(a) : null);

  // ---- 2. 有上限、需求刚好 <= 上限：入库量 = 上限（一次挂号开满）----
  var e = byName('依洛尤单抗注射液');
  ck('依洛尤 理论缺口=2', e && needUnits(e) === 2, e ? needUnits(e) : null);
  ck('依洛尤 需补=2（=上限）', e && dispQty(e) === 2, e ? dispQty(e) : null);

  // ---- 3. 没设单次上限：入库量 = 补满目标天数 ----
  var m = byName('阿司匹林肠溶片');            // 无 rxMax，示例里余量大、不在清单
  if(m){ m.stockBase = 0; m.stockDate = todayStr(); }
  render();
  ck('阿司匹林 无上限 需补=30', m && dispQty(m) === 30, m ? dispQty(m) : null);

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
  // 颜色信号还在：氨氯地平只剩 1 天 → 偏少(soon)，卡片 data-lv 应为 soon
  var c1 = card('苯磺酸氨氯地平片');
  ck('氨氯地平卡片是偏少色', c1 && c1.getAttribute('data-lv') === 'soon',
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
  ck('卡片 tag 是「偏少」', /苯磺酸氨氯地平片\s*偏少/.test(bodyTxt), '');
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
  ck('弹窗计划含 5 种药（阿司匹林被改成 0 余量后也进清单）', plan.length === 5, plan.length);
  ck('弹窗 氨氯地平入库=7', pm['苯磺酸氨氯地平片'] === 7, pm['苯磺酸氨氯地平片']);
  ck('弹窗 阿托伐他汀入库=14', pm['阿托伐他汀钙片'] === 14, pm['阿托伐他汀钙片']);
  ck('弹窗 依洛尤入库=2', pm['依洛尤单抗注射液'] === 2, pm['依洛尤单抗注射液']);

  openDisp();
  var sub = q('#dispSub') ? q('#dispSub').textContent : '';
  var rows = q('#dispList') ? q('#dispList').textContent.replace(/\s+/g, ' ').trim() : '';
  ck('弹窗说明点明「一次挂号只能开一次药」', sub.indexOf('一次挂号只能开一次药') >= 0, sub);
  ck('弹窗行内说明按上限开满', /单次限\s*7 .*?按上限开满/.test(rows), rows.slice(0, 200));
  ck('弹窗行内说明给出趟数', /共需\s*29.*?要开\s*5\s*次/.test(rows), rows.slice(0, 300));

  // ---- 5. 真点一次「已经开药」，看库存是不是按上限加 ----
  var before = {
    a: stockNow(byName('苯磺酸氨氯地平片')),
    b: stockNow(byName('阿托伐他汀钙片')),
    c: stockNow(byName('依洛尤单抗注射液'))
  };
  submitDisp({ preventDefault: function(){} });
  var after = {
    a: byName('苯磺酸氨氯地平片').stockBase,
    b: byName('阿托伐他汀钙片').stockBase,
    c: byName('依洛尤单抗注射液').stockBase
  };
  ck('入库后 氨氯地平 = 1+7 = 8', after.a === 8, JSON.stringify([before.a, after.a]));
  ck('入库后 阿托伐他汀 = 3+14 = 17', after.b === 17, JSON.stringify([before.b, after.b]));
  ck('入库后 依洛尤 = 1+2 = 3', after.c === 3, JSON.stringify([before.c, after.c]));
  ck('提示还剩 1 种没到位', /还有\s*1\s*种/.test(R.alerts.join(' | ')), R.alerts.join(' | '));

  // ---- 6. 缺药但补满后离开清单 ----
  var asp = byName('阿司匹林肠溶片');
  ck('阿司匹林补满后 30 天、已不在清单',
     asp && daysLeft(asp) > settings.alertDays && !inRefill(asp), asp ? daysLeft(asp) : null);

  // ---- 7. 批量调整存量（整个药箱统一 ±N 天）----
  // 重置到已知状态（前面几段动过数据），并补一味「没设用法」的药来验跳过分支。
  var demo2 = [
    { name:'苯磺酸氨氯地平片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:7 },                  // 现 1，每日 1
    { name:'阿托伐他汀钙片', spec:'20mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:21, stockDate:agoD(18), rxMax:14 },                 // 现 3，每日 1
    { name:'盐酸二甲双胍缓释片', spec:'0.5g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
      stockBase:60, stockDate:agoD(20) },                           // 现 20，每日 2 → 7 天=14 片
    { name:'阿司匹林肠溶片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:90, stockDate:agoD(10) },                           // 现 80，每日 1
    { name:'依洛尤单抗注射液', spec:'140mg/支', perDose:1, unit:'支', cycleDays:14, timesPerCycle:1,
      stockBase:2, stockDate:agoD(20), rxMax:2 },                   // 现 1，每 14 天 1 支 → 7 天=0.5 支
    { name:'维生素D滴剂', spec:'400IU', perDose:1, unit:'粒', cycleDays:1, timesPerCycle:0,
      stockBase:30, stockDate:todayStr() }                          // 没设用法 → 应被跳过
  ];
  demo2.forEach(function(d){ d.id = uid(); });
  setMeds(demo2); save(); render();
  ck('批量前置：6 味药已就位', meds.length === 6, meds.length);

  // --- 7a. 纯计算：天数 → 数量，各按自己的用法换算 ---
  var upR = {}, dnR = {};
  bulkRows(7, 1).forEach(function(r){ upR[r.m.name] = r; });
  bulkRows(7, -1).forEach(function(r){ dnR[r.m.name] = r; });
  ck('氨氯地平 现 1 片 → 加 7 天 = 8 片', upR['苯磺酸氨氯地平片'].next === 8, upR['苯磺酸氨氯地平片'].next);
  ck('氨氯地平 减 7 天 → 0 且标记已截断',
     dnR['苯磺酸氨氯地平片'].next === 0 && dnR['苯磺酸氨氯地平片'].floored === true,
     JSON.stringify(dnR['苯磺酸氨氯地平片']));
  ck('二甲双胍 每日 2 片 → 加 7 天 = 34（+14）', upR['盐酸二甲双胍缓释片'].next === 34,
     upR['盐酸二甲双胍缓释片'].next);
  ck('依洛尤 每 14 天 1 支 → 加 7 天 = 1.5（+0.5）', upR['依洛尤单抗注射液'].next === 1.5,
     upR['依洛尤单抗注射液'].next);
  ck('阿司匹林 现 80 → 加 7 天 = 87', upR['阿司匹林肠溶片'].next === 87, upR['阿司匹林肠溶片'].next);
  ck('没设用法的那味被跳过', upR['维生素D滴剂'].skip === true && dnR['维生素D滴剂'].skip === true, '');
  ck('跳过的药不会被改动（next 仍 30）', upR['维生素D滴剂'].next === 30, upR['维生素D滴剂'].next);

  // --- 7b. 弹窗预览（2026-10-12：可勾选 + 顺序同主页 + 档位色）---
  // 这一段的 demo2 正好能验顺序：录入顺序是 氨氯/阿托/二甲/阿司匹林/依洛尤/维D，
  // 而按档位排完后 依洛尤（每 14 天 1 支，剩 14 天 = 偏少）要排到阿司匹林（剩 80 天 = 充足）前面。
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
  ck('排序确实按档位重排了（依洛尤排在阿司匹林前面，不是录入顺序）',
     bulkOrder.indexOf('依洛尤单抗注射液') < bulkOrder.indexOf('阿司匹林肠溶片'),
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
  ck('「偏少」的默认勾上（依洛尤）', ckOf('依洛尤单抗注射液').checked === true, '');
  ck('「偏少」的默认勾上（氨氯地平）', ckOf('苯磺酸氨氯地平片').checked === true, '');
  ck('「充足」的默认不勾（阿司匹林）', ckOf('阿司匹林肠溶片').checked === false, '');
  ck('「充足」的默认不勾 → 那行只写「未勾选」、不给数字', afterOf('阿司匹林肠溶片') === '未勾选',
     afterOf('阿司匹林肠溶片'));
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
  setCk('阿司匹林肠溶片', true);
  ck('手动勾上充足的药 → 已勾选 5 / 5', /已勾选 5 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('手动勾上后那行照常给数字', /＋7 天/.test(afterOf('阿司匹林肠溶片')), afterOf('阿司匹林肠溶片'));

  // ★ 取消勾选一味（偏少的） → 那一行不再给数字，汇总与按钮当场跟着变
  setCk('盐酸二甲双胍缓释片', false);
  ck('取消勾选后那行写「未勾选」，不再给数字', afterOf('盐酸二甲双胍缓释片') === '未勾选',
     afterOf('盐酸二甲双胍缓释片'));
  ck('取消勾选 → 底部变成已勾选 4 / 5', /已勾选 4 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  ck('取消勾选 → 按钮文案变成 4 种', /勾选的 4 种各增加 7 天/.test(q('#btnBulkPlus').textContent),
     q('#btnBulkPlus').textContent);
  ck('没被取消的行照旧给数字', /＋7 天/.test(afterOf('苯磺酸氨氯地平片')), afterOf('苯磺酸氨氯地平片'));

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
         + byName('维生素D滴剂').id + '"]');
       return c.disabled === true && c.checked === false;
     })(), '');

  // --- 7c. 真的执行：增加 ---
  var alertsBefore = R.alerts.length;
  applyBulk(1);
  ck('加 7 天：氨氯地平 8', byName('苯磺酸氨氯地平片').stockBase === 8,
     byName('苯磺酸氨氯地平片').stockBase);
  ck('加 7 天：阿托伐他汀 10', byName('阿托伐他汀钙片').stockBase === 10,
     byName('阿托伐他汀钙片').stockBase);
  ck('加 7 天：二甲双胍 34', byName('盐酸二甲双胍缓释片').stockBase === 34,
     byName('盐酸二甲双胍缓释片').stockBase);
  ck('加 7 天：依洛尤 1.5', byName('依洛尤单抗注射液').stockBase === 1.5,
     byName('依洛尤单抗注射液').stockBase);
  ck('加 7 天：没设用法的那味没被动（仍 30）', byName('维生素D滴剂').stockBase === 30,
     byName('维生素D滴剂').stockBase);
  ck('基准日滚到今天', byName('苯磺酸氨氯地平片').stockDate === todayStr(),
     byName('苯磺酸氨氯地平片').stockDate);
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
  var five = ['苯磺酸氨氯地平片','阿托伐他汀钙片','盐酸二甲双胍缓释片','阿司匹林肠溶片','依洛尤单抗注射液'];
  ck('减 100 天：5 味全到 0，没有负数',
     five.every(function(n){ return byName(n).stockBase === 0; }),
     five.map(function(n){ return byName(n).stockBase; }).join('/'));
  ck('减到 0 的药 level 是「已用完」', byName('阿司匹林肠溶片') && level(byName('阿司匹林肠溶片')) === 'out',
     level(byName('阿司匹林肠溶片')));
  ck('没设用法的那味仍是 30', byName('维生素D滴剂').stockBase === 30,
     byName('维生素D滴剂').stockBase);
  ck('提示里说明了被减到 0 的味数', /已减到 0/.test(R.alerts[R.alerts.length - 1]),
     R.alerts[R.alerts.length - 1]);

  // --- 7e. 「增加」必须和单药「又买了药」同一口径（stockNow 基础上加） ---
  ck('批量加完后 stockNow == stockBase（基准日=今天，没被重复扣）',
     byName('维生素D滴剂').stockBase === stockNow(byName('维生素D滴剂')), '');

  // --- 7f. ★ 2026-10-12 新增：只勾一部分 → 没勾的必须一根手指都不碰 ---
  // 此刻 5 味都已被减到 0，维生素D 仍是 30。
  // 此刻 5 味都已用完 → 默认就把这 5 味全勾上了（「已用完」在默认档里），先把别的取消
  openBulk();
  ['苯磺酸氨氯地平片','阿托伐他汀钙片','盐酸二甲双胍缓释片','依洛尤单抗注射液']
    .forEach(function(n){
      var c = document.querySelector('#bulkList input.bulk-ck[data-id="' + byName(n).id + '"]');
      c.checked = false;
      c.dispatchEvent(new Event('change', {bubbles:true}));
    });
  ck('只留阿司匹林勾选（已勾选 1 / 5）', /已勾选 1 \/ 5 种/.test(q('#bulkPickHint').textContent),
     q('#bulkPickHint').textContent);
  var alerts2 = R.alerts.length;
  applyBulk(1);
  ck('只勾一种 → 它确实变了（0 → 7）', byName('阿司匹林肠溶片').stockBase === 7,
     byName('阿司匹林肠溶片').stockBase);
  ck('没勾的没被动（氨氯地平仍 0）', byName('苯磺酸氨氯地平片').stockBase === 0,
     byName('苯磺酸氨氯地平片').stockBase);
  ck('没勾的没被动（二甲双胍仍 0）', byName('盐酸二甲双胍缓释片').stockBase === 0,
     byName('盐酸二甲双胍缓释片').stockBase);
  ck('没设用法的没被动（维生素D 仍 30）', byName('维生素D滴剂').stockBase === 30,
     byName('维生素D滴剂').stockBase);
  ck('提示只说改了 1 种', /已把 1 种药的存量增加 7 天的量/.test(R.alerts.slice(alerts2).join(' | ')),
     R.alerts[R.alerts.length - 1]);

  // 一种都不勾就点确认 → 拦下，且不改任何数据（按钮虽已禁用，这里直接调函数兜底）
  openBulk();
  q('#btnBulkNone').click();
  var alerts3 = R.alerts.length;
  applyBulk(1);
  ck('一种都没勾时点确认被拦下并提示', /还没勾选任何药/.test(R.alerts.slice(alerts3).join(' | ')),
     R.alerts[R.alerts.length - 1]);
  ck('被拦下时数据没动（阿司匹林仍 7）', byName('阿司匹林肠溶片').stockBase === 7,
     byName('阿司匹林肠溶片').stockBase);
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

  R.meds = meds.map(function(x){ return x.name + '|qty=' + dispQty(x) + '|cap=' + rxCap(x); });

  var pre = document.createElement('pre');
  pre.id = '__T';
  pre.textContent = JSON.stringify(R);
  document.body.appendChild(pre);
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
