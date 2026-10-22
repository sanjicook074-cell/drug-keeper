# -*- coding: utf-8 -*-
"""
drug-keeper：「需补」是否跟着单次开药上限走 —— 真实 DOM 驱动测试。

做法：把 index.html 复制成一份临时副本，在 </body> 前注入检查脚本，
用无头 Chrome --dump-dom 取回渲染后的真实 DOM，解析 #__T 里的 JSON 结果。

断言口径（2026-09-28 用户要求）：一次挂号只能开一次药 → 卡片上的「需补」
与实际入库量必须是**同一个数**，并且受单次上限约束（有上限 → 上限值）。
"""
import base64
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
(async function(){
  var R = { alerts: [], cases: [], prompts: [], confirms: [], natives: [] };
  window.__T = R;
  // ★ 2026-10-23 起页面里不再有原生 alert / confirm / prompt（用户要求"独立弹窗"）。
  //   这三个桩改成**记违规**：谁还在用原生弹窗，R.natives 就非空，最后那条断言会红。
  window.alert = function(m){ R.natives.push('alert: ' + String(m)); };
  window.prompt = function(m){ R.natives.push('prompt: ' + String(m)); return null; };
  window.confirm = function(m){ R.natives.push('confirm: ' + String(m)); return true; };
  // 页面里的弹窗换成 askAlert / askConfirm / askText / askChoice（都返回 Promise）。
  // 这套测的是底方/开药业务，弹窗本身另有专门断言（test_box.py 第 20 节）——
  // 这里只记账不真弹，免得每条断言都要替用户点一次按钮。
  function _txt(o){
    return typeof o === 'string' ? o : ((o.title || '') + ' ' + (o.sub || ''));
  }
  askAlert = function(o){ R.alerts.push(_txt(o)); return Promise.resolve(); };
  askConfirm = function(o){
    R.confirms.push(_txt(o));
    return Promise.resolve(R.confirmAnswer !== false);
  };
  askText = function(o){ R.prompts.push(_txt(o)); return Promise.resolve(R.promptValue == null ? '' : R.promptValue); };
  // 「这份底方是谁的」现在是三选一弹窗（不是 confirm）：create / keep / null
  R.choiceAnswer = 'create';
  askChoice = function(o){
    R.confirms.push(_txt(o) + ' [选项: ' + (o.choices || []).map(function(c){ return c.text; }).join(' / ') + ']');
    return Promise.resolve(R.choiceAnswer);
  };
  R.confirmAnswer = true;
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
     await (async function(){
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
  //   2026-10-13 起「一次最多能开多少」是**必填**（见 8e），所以这一批夹具也必须带上 rxMax ——
  //   否则 submitMed 会被拦下、断言「值没变」会**假绿**（8b 那种「只改备注 → 基准不动」本来就该绿的更危险）。
  var demo3 = [
    { name:'编弹窗对账片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:30 },              // 扣到今天 = 1 片 / 剩 1 天
    { name:'每日两片对账', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:4, stockDate:todayStr(), rxMax:60 },             // 用户举的例子：每日 2 片、4 片 = 2 天
    { name:'三次两片对账', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:3,
      stockBase:12, stockDate:todayStr(), rxMax:60 },            // 每次 2 片×每天 3 次 → 12 片 = 2 天
    { name:'零头对账片', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:5, stockDate:todayStr(), rxMax:30 },             // 5 片 ÷ 每天 2 片 → 2 天多一点
    { name:'半天对账片', spec:'5mg', perDose:2, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:1, stockDate:todayStr(), rxMax:30 },             // 1 片 < 每天 2 片 → 不够 1 整天
    { name:'没设用法片', spec:'—', perDose:1, unit:'片', cycleDays:1, timesPerCycle:0,
      stockBase:30, stockDate:todayStr(), rxMax:30 }             // 没设用法 → 不递减
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

  // --- 8e. ★ 2026-10-13 用户要求：「添加药品」里只有「规格」「备注」选填，**其余全必填** ---
  //   注意这里验的是 **JS 那一层**：直接调 submitMed() 会跳过 HTML 的 required
  //   （那是浏览器在 submit 事件**之前**拦的，脚本调函数时根本不走那道门）。
  //   所以本段专测兜底：空着 / 填 0 必须被拦、且一个字段都不许写进药箱。
  //   HTML 上那 8 个 required 由 main() 里的源码级断言负责（那边也管「规格/备注不许带 required」）。
  openEditor(null);
  var before8e = meds.length;
  fmed.name.value = '必填校验用片';
  fmed.spec.value = '';
  fmed.note.value = '';                        // 这两格是选填，留空
  fmed.perDose.value = 1;
  fmed.unit.value = '片';
  fmed.cycleDays.value = 1;
  fmed.timesPerCycle.value = 1;
  fmed.stockBase.value = 0;
  fmed.stockDate.value = todayStr();
  fmed.rxMax.value = '';                       // 唯一会空着的必填格

  var al0 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('单次上限空着 → 被拦下，没写进药箱', meds.length === before8e, meds.length - before8e);
  ck('提示点名了「一次最多能开多少」',
     /一次最多能开多少/.test(R.alerts.slice(al0).join(' | ')), R.alerts[R.alerts.length - 1]);
  ck('被拦下时弹窗还开着（没当成功）', q('#dlgMed').open, '');

  // 每次用量填 0 = 没填用法（卡片会算不出「剩几天」）→ 照样拦
  fmed.rxMax.value = '30';
  fmed.perDose.value = 0;
  var al1 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('每次用量填 0 → 被拦下', meds.length === before8e, meds.length - before8e);
  ck('提示写清了「每次用量（要大于 0）」',
     /每次用量（要大于 0）/.test(R.alerts.slice(al1).join(' | ')), R.alerts[R.alerts.length - 1]);

  // 每周期几次填 0 → 同理
  fmed.perDose.value = 1;
  fmed.timesPerCycle.value = 0;
  var al2 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('每周期几次填 0 → 被拦下', meds.length === before8e, meds.length - before8e);
  ck('提示写清了「每周期几次（要大于 0）」',
     /每周期几次（要大于 0）/.test(R.alerts.slice(al2).join(' | ')), R.alerts[R.alerts.length - 1]);

  // 余量：填 0 可以（刚好吃完），空着不行
  fmed.timesPerCycle.value = 1;
  fmed.stockBase.value = '';
  var al3 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('余量空着 → 被拦下', meds.length === before8e, meds.length - before8e);
  ck('提示写明余量「填 0 可以，但不能空」',
     /余量（填 0 可以，但不能空）/.test(R.alerts.slice(al3).join(' | ')),
     R.alerts[R.alerts.length - 1]);

  // 单次上限填 0 = 老口径的「不限」，这格现在必填 → 也要拦
  fmed.stockBase.value = 0;
  fmed.rxMax.value = '0';
  var al4 = R.alerts.length;
  submitMed({ preventDefault: function(){} });
  ck('单次上限填 0 → 被拦下（不再当「不限」）', meds.length === before8e, meds.length - before8e);
  ck('同上，提示仍是「一次最多能开多少（要大于 0）」',
     /一次最多能开多少（要大于 0）/.test(R.alerts.slice(al4).join(' | ')),
     R.alerts[R.alerts.length - 1]);

  // 都填齐了 → 规格 / 备注留空照样存得下去（这两格是选填）
  fmed.rxMax.value = '30';
  submitMed({ preventDefault: function(){} });
  ck('填齐后正常入库（规格/备注留空不影响）', meds.length === before8e + 1, meds.length - before8e);
  var mk = byName('必填校验用片');
  ck('入库后 rxMax=30，而 spec / note 是空串',
     !!mk && mk.rxMax === 30 && mk.spec === '' && mk.note === '',
     mk ? [mk.rxMax, JSON.stringify(mk.spec), JSON.stringify(mk.note)].join(' / ') : '(没进来)');
  ck('保存成功后弹窗已关闭', !q('#dlgMed').open, '');

  // 老存档里 rxMax=0（旧的「不限」）→ 编辑时那格必须留空并提示要补填，
  // 这是「原来是不限」那批药唯一的迁移路径，别让它静默变成 0 上限。
  var legacy = byName('必填校验用片');
  legacy.rxMax = 0;
  openEditor(legacy.id);
  ck('老药 rxMax=0 → 编辑时那格留空（等人补填）', fmed.rxMax.value === '', '「' + fmed.rxMax.value + '」');
  ck('并且换了 placeholder 说明为什么是空的',
     /原来是不限/.test(fmed.rxMax.placeholder), fmed.rxMax.placeholder);
  q('#dlgMed').close();

  // ★ 上面那些都是「JS 兜底」被拦下的样子。HTML 上那 8 个 required 到底有没有生效？
  //   直接调 submitMed() 会**绕过**它，所以只能拿浏览器自己的 checkValidity() 来问 ——
  //   这才是「真人在页面上点保存时会被浏览器拦下」的证据。
  openEditor(null);
  var formEl = q('#formMed');
  fmed.name.value = '必填校验用片';
  fmed.spec.value = '';
  fmed.note.value = '';
  fmed.rxMax.value = '';
  ck('新增时整表不合法（上限空着 → 原生 required 会拦）',
     formEl.checkValidity() === false, formEl.checkValidity());
  var invalids = Array.prototype.map.call(formEl.querySelectorAll('input'), function(el){
    return el.name + ':' + el.validity.valid;
  }).filter(function(s){ return /:false$/.test(s); });
  ck('不合法的那格就是 rxMax（其余全过）',
     invalids.length === 1 && invalids[0] === 'rxMax:false', invalids.join('、') || '(全过)');
  fmed.rxMax.value = '30';
  ck('补上限后整表合法（规格/备注仍留空）', formEl.checkValidity() === true, formEl.checkValidity());
  // 把「余量」清空 → 又该不合法（它能填 0，但不能空）
  fmed.stockBase.value = '';
  ck('余量清空 → 整表又不合法', formEl.checkValidity() === false, formEl.checkValidity());
  fmed.stockBase.value = '0';
  ck('余量填 0 → 合法（0 是有效值，不是"没填"）', formEl.checkValidity() === true, formEl.checkValidity());
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

  // ---- 10. ★ 底方录入（2026-10-12 起；2026-10-13 收敛为「只走助手通道」）----
  //   用户原话：「用户能直接拍照 来批量录入开过的药品 然后程序引导用户确认每种药都能用多少天
  //   …在我的药品里 现在只有添加第一种药的按钮 应该再加一个底方录入药品的按钮」。
  //   ★ 认照片那一步交给助手，**网页里彻底不做** —— 2026-10-13 用户实测后，浏览器端 OCR
  //     那条路整条删掉了（认不准还得人一格格就地改，比直接填还慢）。页面只负责：
  //     接住助手给的 #rx= 链接 / 把人手头的文字解析成药品行 / 确认天数后入库。
  //   ★ 下面的每一行都**照真底方的版式**写（药名(商品名)[厂家][规格] 数量 单价 用量 频次 用法），
  //     但药名一律是中性示例 —— 2026-10-12 用户要求「全换成中性药名」：真底方原文属于
  //     「已经输入过的信息」，不该跟着开源代码分发（他 2026-09-28 对内置示例数据就是这条要求）。
  //     版式特征一个没丢：圆括号罗马数字、方括号商品名/厂家、`*每盒数`、瓶/盒/支、
  //     用量÷规格 = 每次几片、每日 N 次 / 每两周 / 每晚 / 外用 / 皮下注射。
  //     本机另有一份**不进仓库**的真药名名单（`_check/_tmp_deny_names.txt`，被 .gitignore 挡着），
  //     `main()` 拿它扫 index.html —— 以后谁再顺手粘一段真底方进来，断言当场红。

  // 10a. 两个入口
  ck('顶部工具栏有「底方录入」按钮', !!q('#btnRx'), '');
  setMeds([]); save(); render();
  ck('空药箱里也加了「底方录入」（和「添加第一种药」并排）',
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

  // 「每周2次」「每月1次」：量词前面不写数字的写法。这两条原来会整条漏掉、
  // 落到默认的「每日 1 次」上（错值，不是仅告警），2026-10-12 修。
  var b1 = rxParseLine('甲氨蝶呤片[2.5mg*16]  1盒  3.20  2.5mg  每周2次  口服');
  ck('「每周2次」→ 每 7 天 2 次（不是每日 1 次）', b1.cycleDays === 7 && b1.timesPerCycle === 2,
     b1.cycleDays + ' / ' + b1.timesPerCycle);
  ck('「每周2次」：16 片 ÷ 每天 2/7 片 → 够 56 天', rxDaysOf(b1) === 56, rxDaysOf(b1));
  var b2 = rxParseLine('阿仑膦酸钠片[70mg*4]  1盒  32.00  70mg  每月1次  口服');
  ck('「每月1次」→ 每 28 天 1 次', b2.cycleDays === 28 && b2.timesPerCycle === 1,
     b2.cycleDays + ' / ' + b2.timesPerCycle);
  ck('「每月1次」：4 片 ÷ 每天 1/28 片 → 够 112 天', rxDaysOf(b2) === 112, rxDaysOf(b2));
  ck('「每周2次」不再误报「没认出频次」', b1.warn === '', '「' + b1.warn + '」');

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
  await openRx();
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
  await openRx(back.rows, '助手');
  ck('预填的弹窗里药已经摆好、天数也算好了',
     document.querySelectorAll('#rxList .rx-row').length === 1
     && q('#rxList .rx-row:nth-child(1) .rx-days-in').value === '14',
     document.querySelectorAll('#rxList .rx-row').length + ' 行');
  ck('说明里写清「这是你发给它的底方读出来的，你核对一下」',
     /按你发给它的底方读好了/.test(q('#rxSub').textContent), q('#rxSub').textContent.slice(0, 36));
  q('#dlgRx').close();

  // 名称空着的行：拦下，不许静默入库
  await openRx([{ name:'', unit:'片', perDose:1, cycleDays:1, timesPerCycle:1, total:10 }]);
  var n0 = meds.length;
  submitRx({ preventDefault: function(){} });
  ck('名称空着的那味被拦下（没入库）', meds.length === n0, meds.length - n0);
  ck('并且给了提示', /还没填药品名称/.test(R.alerts[R.alerts.length - 1]),
     R.alerts[R.alerts.length - 1]);
  q('#dlgRx').close();

  // 10f. ★ 2026-10-13 事故后加：「这份底方是谁的」+ 重复的药挑出来、只加新出现的。
  //   起因：手机端助手把底方上的一味药**换成了另一个同类药**，同时又**漏掉一味真的** ——
  //   药味总数没变，所以"8 种全部录入"那句自检**看上去是过的**（总数对上 ≠ 认对）。
  //   页面这边能管住两件事：① 抄下底方抬头（patient）来**定位药箱**；
  //                       ② 跟这个人的药箱比一遍，**重复的取消勾选、只加新出现的**。
  await (async function(){
    R.confirms = [];
    var backTo = currentId;

    // 造一个人「乙」，药箱里已经有「维生素C片」；底方里既有它（重复），也有新的一味
    var yi = { id: uid(), name: '乙', note: '', meds: [
      { id: uid(), name:'维生素C片', spec:'100mg', perDose:1, unit:'片', cycleDays:1,
        timesPerCycle:1, stockBase:20, stockDate: todayStr(), rxMax:30 }
    ] };
    people.push(yi); save(); render();

    await openRx([ { name:'维生素C片', spec:'100mg', unit:'片', perDose:1, cycleDays:1,
               timesPerCycle:1, total:30 },
             { name:'蒙脱石散', spec:'3g*10', unit:'袋', perDose:3, cycleDays:1,
               timesPerCycle:3, total:30 } ],
           '助手',
           { name:'乙', sex:'女', age:'58', dx:'腹泻', date:'2026-10-12',
             hospital:'某某医院', rxNo:'A1234567' });

    ck('底方带姓名 → 自动切到那个人的药箱（不再记到当前人身上）',
       (curPerson() || {}).name === '乙', (curPerson() || {}).name);
    ck('重复的那味也照样列出来（不偷偷删掉）',
       document.querySelectorAll('#rxList .rx-row').length === 2,
       document.querySelectorAll('#rxList .rx-row').length);
    var c1 = q('#rxList .rx-row:nth-child(1) .rx-ck');
    var c2 = q('#rxList .rx-row:nth-child(2) .rx-ck');
    ck('药箱里已有的那味 → 自动取消勾选', !!c1 && c1.checked === false, c1 && c1.checked);
    ck('新出现的那味 → 保持勾选', !!c2 && c2.checked === true, c2 && c2.checked);
    ck('已有的那行挂了「药箱里已有」角标', !!q('#rxList .rx-row:nth-child(1) .rx-duptag'), '');
    ck('新的那行没有那个角标', !q('#rxList .rx-row:nth-child(2) .rx-duptag'), '');
    var wt = q('#rxWho').textContent.replace(/\s+/g, ' ');
    ck('弹窗顶上写清「这份底方是谁的」（姓名/性别/年龄/医院/日期）',
       /乙/.test(wt) && /女/.test(wt) && /58/.test(wt) && /某某医院/.test(wt)
       && /2026-10-12/.test(wt), wt.slice(0, 70));
    ck('诊断也照抄显示出来了', /腹泻/.test(wt), wt.slice(0, 110));
    ck('去重结果写清：已有 1 味（已取消勾选）、本次新增 1 味',
       /已有/.test(wt) && /新增 1/.test(wt), wt.slice(0, 150));
    ck('底部汇总也说清"另有 1 味已有、默认没勾"',
       /另有/.test(q('#rxSum').textContent) && /不会重复入库/.test(q('#rxSum').textContent),
       q('#rxSum').textContent.slice(0, 90));

    var before = meds.length;
    submitRx({ preventDefault: function(){} });
    ck('只把新的那味加进药箱（重复的没塞第二遍）', meds.length === before + 1, meds.length - before);
    ck('药箱里「维生素C片」仍然只有 1 条',
       meds.filter(function(x){ return x.name === '维生素C片'; }).length === 1, '');
    ck('新的那味进来了', !!byName('蒙脱石散'), !!byName('蒙脱石散'));

    // 同一张底方再发一次（复诊/重发）→ 这回两味都已有，一味都不该勾
    await openRx([ { name:'维生素C片', spec:'100mg', unit:'片', perDose:1, cycleDays:1, timesPerCycle:1, total:30 },
             { name:'蒙脱石散', spec:'3g*10', unit:'袋', perDose:3, cycleDays:1, timesPerCycle:3, total:30 } ],
           '助手', { name:'乙' });
    ck('同一张底方重发 → 两味都不勾（没有新的可加）',
       document.querySelectorAll('#rxList .rx-ck:checked').length === 0,
       document.querySelectorAll('#rxList .rx-ck:checked').length);
    ck('全重复时确认按钮 disabled', q('#btnRxOk').disabled === true, q('#btnRxOk').disabled);
    q('#dlgRx').close();

    // 底方没写姓名 → **不乱猜、不切人**，但必须提醒去确认
    var keepId = currentId;
    await openRx([ { name:'开塞露', unit:'支', perDose:1, cycleDays:1, timesPerCycle:1, total:1 } ]);
    ck('底方没写姓名 → 不动当前的人', currentId === keepId, currentId === keepId);
    ck('并且提醒「底方上没写姓名、自己确认是谁的」',
       /没写姓名/.test(q('#rxWho').textContent), q('#rxWho').textContent.slice(0, 50));
    q('#dlgRx').close();

    // 底方上的人药箱里没有 → 问一句再新建（不悄悄替他建）
    var nPeople = people.length;
    await openRx([ { name:'开塞露', unit:'支', perDose:1, cycleDays:1, timesPerCycle:1, total:1 } ],
           '助手', { name:'丙' });
    ck('药箱没这个人 → 弹框问过（把名字带上了）',
       R.confirms.length >= 1 && /丙/.test(R.confirms[R.confirms.length - 1]),
       R.confirms[R.confirms.length - 1] || '(没问)');
    ck('同意后新建这个人并切过去',
       (curPerson() || {}).name === '丙' && people.length === nPeople + 1,
       ((curPerson() || {}).name) + ' / ' + people.length);
    q('#dlgRx').close();

    // 用户选了「记到当前人名下」→ 不建人，但要说清"仍记在当前人名下"
    // （这个弹窗是二选一：原来用 confirm 的"确定/取消"表达，现在两个按钮各写各的动作）
    R.choiceAnswer = 'keep';
    nPeople = people.length;
    await openRx([ { name:'开塞露', unit:'支', perDose:1, cycleDays:1, timesPerCycle:1, total:1 } ],
           '助手', { name:'戊' });
    ck('选「记到当前人名下」→ 不新建人', people.length === nPeople, people.length - nPeople);
    ck('选「记到当前人名下」→ 明说仍记在当前人名下，别让人以为记对了',
       /没有新建人/.test(q('#rxWho').textContent), q('#rxWho').textContent.slice(0, 80));
    R.choiceAnswer = 'create';
    q('#dlgRx').close();

    // 编码往返：patient 要跟着一起进去
    var e2 = rxEncode([{ name:'开塞露', unit:'支', perDose:1, cycleDays:1, timesPerCycle:1, total:1 }],
                      { name:'丁', sex:'男', age:'70' });
    var b2 = JSON.parse(decodeURIComponent(escape(atob(e2.replace(/-/g, '+').replace(/_/g, '/')))));
    ck('rxEncode 会把 patient 一起编进去（链接里带着"这是谁的底方"）',
       !!b2.patient && b2.patient.name === '丁', JSON.stringify(b2.patient || null));

    // 还原：切回原来那个人，别影响后面的断言
    switchPerson(backTo);
  })();

  // 10g. ★ 2026-10-13 事故后加（第二轮）：**对账依据必须看得见**。
  //   10f 管的是"这份底方是谁的"和"重复的挑出来"；这一段管的是**"你到底读全了没有"**。
  //   事故里助手认错一味 + 漏一味、药味总数还是 8 —— 因为**压根没人对过账**。
  //   现在要求它把「每张几味 / 单据金额 / 自己算的合计」交上来（sheets），页面逐张核并显示。
  //   ★ 核心不是"页面能验真"（它没照片，验不了），而是**"有没有人核过"从此看得见**：
  //     缺依据不能沉默（沉默会被读成"没问题"），对不上要标红并说"先别入库"。
  await (async function(){
    var backTo2 = currentId;
    var A = function(){ return q('#rxAudit'); };
    var txt = function(){ return A().textContent.replace(/\s+/g, ' '); };
    var ROWS = [ { name:'测试甲片', unit:'片', perDose:1, cycleDays:1, timesPerCycle:1, total:10 },
                 { name:'测试乙胶囊', unit:'粒', perDose:1, cycleDays:1, timesPerCycle:1, total:20 } ];

    // ① 交了 sheets、逐张都对得上 → 显示 ✓，不是报错样式
    await openRx(ROWS, '助手', { name:'己' },
           [ { i:1, n:1, amount:20.45, sum:20.45 }, { i:2, n:1, amount:6.58, sum:6.58 } ]);
    ck('交了对账且都对得上 → 显示对账块，且不是报错样式',
       A().hidden === false && /AI 交了对账/.test(txt()) && A().className.indexOf('bad') < 0,
       A().className + ' | ' + txt().slice(0, 60));
    ck('逐张写出「第 N 张 · 几味 · 单据 = 算出来」',
       /第 1 张/.test(txt()) && /第 2 张/.test(txt()) && /20\.45/.test(txt()) && /6\.58/.test(txt()),
       txt().slice(0, 120));
    ck('并说清这是助手自报的数、页面只能核它自不自洽',
       /自不自洽/.test(txt()), txt().slice(-70));

    // ② 某张 sum ≠ amount → 标红 + 明说"先别入库"
    await openRx(ROWS, '助手', { name:'己' },
           [ { i:1, n:1, amount:20.45, sum:38.21 }, { i:2, n:1, amount:6.58, sum:6.58 } ]);
    ck('某张对不上账 → 整块变报错样式', A().className.indexOf('bad') >= 0, A().className);
    ck('没通过的账不许显示成 ✓（不能乱标"交了对账"）',
       !/AI 交了对账/.test(txt()), txt().slice(0, 40));
    ck('说清是第几张、两个数都摆出来',
       /第 1 张/.test(txt()) && /38\.21/.test(txt()), txt().slice(0, 120));
    ck('并提示「先别入库」', /先别入库/.test(txt()), txt().slice(-60));

    // ③ 各张味数之和 ≠ 清单条数 → 报行数对不上（丢一味/编一味会在这里露出来）
    await openRx(ROWS, '助手', { name:'己' }, [ { i:1, n:5, amount:20.45, sum:20.45 } ]);
    ck('各张味数之和 ≠ 清单条数 → 报行数对不上',
       /行数对不上/.test(txt()) && A().className.indexOf('bad') >= 0, txt().slice(0, 120));

    // ④ 没交 sheets → **不能沉默**（沉默会被读成"没问题"，那正是这次栽的地方）
    await openRx(ROWS, '助手', { name:'己' });
    ck('没交对账依据 → 明说"没人替你对过"，并给下一步（自己数一遍）',
       A().hidden === false && /没交/.test(txt()) && /替你对过/.test(txt()) && /数一下/.test(txt()),
       txt().slice(0, 110));

    // ⑤ 金额没读到（null）→ 算"对不了账"，绝不冒充通过
    await openRx(ROWS, '助手', { name:'己' }, [ { i:1, n:2, amount:null, sum:null } ]);
    ck('金额读到 null → 说"对不了账"、标红，不冒充通过',
       /对不了账/.test(txt()) && A().className.indexOf('bad') >= 0, txt().slice(0, 90));

    // ⑥ 手动录 / 粘文字进来的 → 没有"助手读数"这回事，不显示这一块
    await openRx();
    ck('手动录（没有药行）→ 不显示对账块', A().hidden === true, A().hidden);
    q('#dlgRx').close();
    // 有药行、但**不是助手给的**（用户自己粘的文字）→ 也不该冒出来质问"没人替你核过"
    await openRx([ { name:'测试丙片', unit:'片', perDose:1, cycleDays:1, timesPerCycle:1, total:5 } ]);
    ck('自己粘文字进来的（不是助手给的）→ 不显示对账块',
       A().hidden === true, A().hidden + ' | ' + txt().slice(0, 50));
    q('#dlgRx').close();

    switchPerson(backTo2);      // 还原，别影响后面的断言
  })();

  function rxFinish(){
    // ★ 用户 2026-10-23：「点删除之后 应该是弹窗来确认 而不是由页面默认方式来实现」。
    //   页面里已经一处原生 alert/confirm/prompt 都没有了（都走 askXxx → 页面内 <dialog>），
    //   这条钉住它：只要谁哪天又写一句 confirm()，这里就红。
    R.cases.push({
      name: '★ 全程没有走浏览器原生 alert / confirm / prompt',
      pass: R.natives.length === 0,
      got: R.natives.slice(0, 3).join(' | ') || '(一次都没有)'
    });
    R.meds = meds.map(function(x){ return x.name + '|qty=' + dispQty(x) + '|cap=' + rxCap(x); });
    var pre = document.createElement('pre');
    pre.id = '__T';
    pre.textContent = JSON.stringify(R);
    document.body.appendChild(pre);
  }

  // 浏览器端 OCR 那条路已删，这里没有异步要等了 —— 直接把结果打出去。
  rxFinish();
})();
"""


def cross_check_link():
    """跨实现往返：**本地工具（Python）**生成的 `#rx=` 链接，真页面必须解得出。

    这是「两条路互通」的地基 —— 谁改了 base64 的口味（要不要 padding、urlsafe、
    UTF-8 怎么包的），这条立刻红。而两边的**单元**测试各自都是绿的（JS 编 JS 解、
    Python 编 Python 解），只有拿一边的产物喂给另一边才照得出来。

    为什么单开一次 Chrome：主探针是一个顺序很长的大脚本，在里面动 `location.hash`
    会牵动它后面所有断言。这里另起一次、用**没插探针的原版 index.html** 带 hash 打开，
    再从渲染出来的 DOM 里数预填行 —— 这也更接近真人点开链接的样子。
    """
    rows_js = [
        {"name": "阿奇霉素片", "spec": "0.5g*60", "unit": "片", "perDose": 2,
         "cycleDays": 1, "timesPerCycle": 2, "total": 120, "note": "", "warn": "", "picked": True},
        {"name": "麝香壮骨膏", "spec": "40mg*6贴", "unit": "贴", "perDose": 1,
         "cycleDays": 1, "timesPerCycle": 1, "total": 6, "note": "外用", "warn": "", "picked": True},
    ]
    # ★ 2026-10-13：patient / sheets 也要一起过这条往返 ——
    #   当年 to_link 只塞了 rows，把对账结果和抬头都丢了（验证做了没传出来 == 没做），
    #   这里就是钉住"别再丢"。patient 的键名必须是英文（name/sex/…）：
    #   工具第一版照抄了 meta 的中文键，页面 patient.name 取不到，顶上照样打印"底方上没写姓名"。
    # ⚠️ 姓名必须用页面里**已存在**的那个人（默认叫「我」）。用一个陌生名字的话，
    #    `rxResolveTarget` 会弹 `confirm('药箱里没这个人，要新建吗？')` —— headless 没人应答，
    #    页面**永久阻塞**、dump-dom 直接 180 秒超时（第一版就栽在这，看着像"chrome 挂了"）。
    patient = {"name": "我", "sex": "女", "age": "58"}
    sheets = [{"i": 1, "n": 1, "amount": 12.52, "sum": 12.52},
              {"i": 2, "n": 1, "amount": 51.98, "sum": 51.98}]
    how = "按同一口径另编的（rx_local 导不进来，这条就不算数）"
    b64 = None
    try:                                   # 首选：真用本地工具那只编码器
        p = os.path.join(ROOT, "tools", "rx_local")
        if p not in sys.path:
            sys.path.insert(0, p)
        import rx_local as RX
        b64 = RX.to_link([
            RX.RxRow(name="阿奇霉素片", spec="0.5g*60", unit="片", per_dose=2,
                     cycle_days=1, times_per_cycle=2, total=120),
            RX.RxRow(name="麝香壮骨膏", spec="40mg*6贴", unit="贴", per_dose=1,
                     cycle_days=1, times_per_cycle=1, total=6, note="外用"),
        ], "", patient, sheets).split("#rx=")[1]
        how = "tools/rx_local 的 to_link"
    except Exception as e:                 # 装不了 Pillow 也别把整套自检拖死
        how = "按同一口径另编的（rx_local 导不进来：%r）" % e
        b64 = base64.urlsafe_b64encode(
            json.dumps({"v": 1, "rows": rows_js, "patient": patient, "sheets": sheets},
                       ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")

    tmpdir = tempfile.mkdtemp(prefix="dk_link_")
    url = "file:///" + SRC.replace("\\", "/") + "#rx=" + b64
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--virtual-time-budget=6000",
           "--user-data-dir=" + os.path.join(tmpdir, "prof"), "--dump-dom", url]
    dom = subprocess.run(cmd, capture_output=True, timeout=180).stdout.decode("utf-8", "replace")
    # ★ 剥掉 <script>：里面还有一份造行的模板字符串，不剥会把模板里的 value="..." 也算进来
    dom_ns = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
    names = re.findall(r'class="rx-name"[^>]*value="([^"]*)"', dom_ns)
    days = re.findall(r'class="rx-days-in"[^>]*value="([^"]*)"', dom_ns)

    def seg(start_id, end_mark):
        """取某块渲染后的纯文本（从 id="X" 到下一个 end_mark 之间）。"""
        i = dom_ns.find('id="%s"' % start_id)
        j = dom_ns.find(end_mark, i) if i >= 0 else -1
        if i < 0 or j <= i:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dom_ns[i:j])).strip()

    who_txt = seg("rxWho", 'id="rxAudit"')
    aud_txt = seg("rxAudit", 'class="rx-howto"')
    shutil.rmtree(tmpdir, ignore_errors=True)
    return [
        {"name": "Python 生成的 #rx= 链接，真页面解得开（两边编码口径一致）[%s]" % how,
         "pass": names == ["阿奇霉素片", "麝香壮骨膏"],
         "got": names if names else "(一行都没解出来)"},
        {"name": "解出来的天数也对（120片 ÷ 每天4片 = 30天；6贴 ÷ 每天1贴 = 6天）",
         "pass": days == ["30", "6"], "got": days if days else "(没有天数)",
         },
        {"name": "链接里的 patient 到了页面（英文键名对得上，不是「底方上没写姓名」）",
         # 键名写成中文时 patient.name 取不到 → 会打印「底方上没写姓名」，
         # 所以「没写姓名」不许出现、「58岁」必须出现，这一条才真的在钉键名。
         "pass": ("58岁" in who_txt) and ("女" in who_txt) and ("没写姓名" not in who_txt),
         "got": who_txt[:90] or "(空)"},
        {"name": "链接里的 sheets 渲染成了对账条（逐张 + 金额）",
         "pass": ("第 1 张" in aud_txt) and ("12.52" in aud_txt) and ("51.98" in aud_txt),
         "got": aud_txt[:110] or "(空)"},
    ]


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

    # ---- 源码级：「添加药品」的必填口径（2026-10-13 用户要求）----
    #   用户原话：「添加药品里 规格和备注是选填 其他的是必填」。
    #   ★ 为什么非要在源码上再钉一遍：`required` 是**浏览器在 submit 事件之前**拦的，
    #     插进页面的 JS 探针根本摸不到它 —— DOM 那段只能验 JS 兜底那一层。
    #     所以「哪几格真的带 required」只在源码上看得到；谁手一滑摘掉一个，这里当场红。
    mform = re.search(r'<form id="formMed">(.*?)</form>', src, flags=re.S)
    fbody = mform.group(1) if mform else ""

    def _attrs(field):
        # ⚠️ 别在名字后面加 \b —— 收尾的是引号 `"`（非单词字符），紧跟空格也是非单词字符，
        #    中间根本没有单词边界，`\b` 会让这条永远匹配不上（第一次就栽在这：8 格全被判「没带 required」）。
        m = re.search(r'<input name="%s"([^>]*)>' % re.escape(field), fbody)
        return m.group(1) if m else None

    want_req = ["name", "perDose", "unit", "cycleDays", "timesPerCycle",
                "stockBase", "stockDate", "rxMax"]
    want_opt = ["spec", "note"]
    bad_req = [k for k in want_req if not (_attrs(k) and "required" in _attrs(k))]
    bad_opt = [k for k in want_opt if _attrs(k) is None or "required" in _attrs(k)]
    data["cases"].append({
        "name": "「添加药品」8 格必填都带 required（药名/每次/单位/每几天/几次/余量/日期/开药上限）",
        "pass": bool(fbody) and not bad_req,
        "got": "、".join(bad_req) if bad_req else "8/8",
    })
    data["cases"].append({
        "name": "「规格」「备注」两格没有 required（选填）",
        "pass": not bad_opt,
        "got": "、".join(bad_opt) if bad_opt else "2/2",
    })
    # 防回潮：上限那格原来写「留空 = 不限」，改成必填后这句必须消失（否则界面自相矛盾）
    data["cases"].append({
        "name": "开药上限那格不再写「留空 = 不限」（它现在必填）",
        "pass": "留空 = 不限" not in src,
        "got": src.count("留空 = 不限"),
    })

    # ---- 源码级：底方这条路只剩「助手通道」（2026-10-13 用户实测后定：浏览器端 OCR 整条删掉）----
    #   ★ 打开页面本身不联网：静态标签里没有任何 src/href 指向外网。
    #   ★ 下面几条是**防回潮**的：那个方向（在浏览器里认照片）试过、跑不通（认不准还得人一格格改），
    #     既然删干净了就别再长回来 —— 谁手一滑加回去，这里当场红。
    n_net = len(re.findall(r'''(?:src|href)\s*=\s*["\']https?://''', src))
    data["cases"].append({
        "name": "静态标签里没有任何外网 src/href（打开页面本身不联网）",
        "pass": n_net == 0,
        "got": n_net,
    })
    data["cases"].append({
        "name": "常驻外链一个都没有（<script src=...> 这种）",
        "pass": "<script src" not in src,
        "got": src.count("<script src"),
    })
    ocr_left = src.count("tesseract") + src.count("Tesseract") + src.count("__rxOcrEngine")
    data["cases"].append({
        "name": "页面里已无任何浏览器端 OCR 痕迹（引擎名 / 假引擎钩子全清）",
        "pass": ocr_left == 0,
        "got": ocr_left,
    })
    photo_left = (src.count('id="rxPhoto"') + src.count("rxLoadPhoto") + src.count("rxExifOrient")
                  + src.count("rxRotateCanvas") + src.count("rxCanvasFrom") + src.count("btnRxShot")
                  + src.count("rxThumb"))
    data["cases"].append({
        "name": "页面里已无「拍 / 选照片」那条路的代码（照片、EXIF 转正、画布旋转全清）",
        "pass": photo_left == 0,
        "got": photo_left,
    })
    data["cases"].append({
        "name": "照片不再进这个页面：能收影像的 file 输入框一个都没有",
        "pass": 'accept="image/*"' not in src,
        "got": src.count('accept="image/*"'),
    })
    data["cases"].append({
        "name": "助手那条路还在：链接编 / 解码（rxEncode / rxDecodeHash）都在",
        "pass": ("rxEncode" in src) and ("rxDecodeHash" in src),
        "got": "都在" if (("rxEncode" in src) and ("rxDecodeHash" in src)) else "(缺一个)",
    })
    data["cases"].append({
        "name": "中文药名认错要吃错药的提醒还在（助手给的也当草稿）",
        "pass": "吃错药" in src,
        "got": "已写明" if "吃错药" in src else "(没写)",
    })
    # 2026-10-23 用户点明口径：「用户看到的就是这个界面，不存在什么这个界面后面还安排着一个后台 AI」。
    # 所以界面要按「用户手上的照片 + 一个他自己打开的 AI 聊天窗口」来写，并且**点名几个 AI**，
    # 让人一眼知道该把照片发给谁。
    data["cases"].append({
        "name": "界面上写明「不认照片」+「没有后台 AI」+ 点名 DeepSeek / Kimi",
        "pass": ("不认照片" in src) and ("后台 AI" in src)
                and ("DeepSeek" in src) and ("Kimi" in src),
        "got": "已写明" if ("不认照片" in src and "后台 AI" in src) else "(没写)",
    })
    data["cases"].append({
        "name": "界面上不再暗示「页面自带一个助手」（这类话一句都不许留）",
        "pass": ("帮你开这个页面的那个" not in src) and ("发给 AI 助手" not in src),
        "got": "已清干净" if ("帮你开这个页面的那个" not in src) else "(还在)",
    })
    data["cases"].append({
        "name": "写明要 AI「把药品名称和用量打成文字」——这一步是用户唯一要做的活",
        "pass": ("药品名称和用量" in src) and ("一行一味" in src),
        "got": "已写明" if ("药品名称和用量" in src) else "(没写)",
    })

    # ---- 源码级：页面里**藏着**一段写给 AI 助手的接口说明（用户 2026-10-13 点名要的）----
    #   「拍照这里只走 agent 的通道，你要在这个页面里藏一个能让 agent 看懂怎么用它的说明」。
    #   它既是给人读源码时能看到的注释，也是一个 DOM 里可达的 #rx-agent-guide 节点。
    ag = re.search(r'<script[^>]*id="rx-agent-guide"[^>]*>(.*?)</script>', src, flags=re.S)
    ag_txt = ag.group(1) if ag else ""
    data["cases"].append({
        "name": "页面里藏着给 AI 助手的接口说明（#rx-agent-guide）",
        "pass": bool(ag) and len(ag_txt) > 400,
        "got": ("%d 字" % len(ag_txt)) if ag else "(没找到)",
    })
    for kw in ("#rx=", "base64url", "name", "spec", "perDose", "cycleDays",
               "timesPerCycle", "total", "picked", "rx_local"):
        data["cases"].append({
            "name": "助手说明里写清了字段 / 口径：%s" % kw,
            "pass": kw in ag_txt,
            "got": "有" if kw in ag_txt else "(缺)",
        })

    # ---- 源码级：2026-10-13 事故后，说明里必须多交代三件事（见 #rx-agent-guide「三条硬规矩」）----
    #   事故：助手把底方上一味药换成了同类药、又漏掉一味真的 —— 总数没变，自检看着是过的。
    for kw in ("patient", "逐字", "别自己删", "每张"):
        data["cases"].append({
            "name": "助手说明里写清了新规矩：%s" % kw,
            "pass": kw in ag_txt,
            "got": "有" if kw in ag_txt else "(缺)",
        })
    data["cases"].append({
        "name": "说明里点明「总数对得上 ≠ 认对了」（这次真正的教训）",
        "pass": ("总数对得上" in ag_txt) and ("≠" in ag_txt),
        "got": "已写明" if "总数对得上" in ag_txt else "(没写)",
    })
    data["cases"].append({
        "name": "说明里交代了「谁的底方」要照抄、抄错会记到别人名下",
        "pass": ("照抄" in ag_txt) and ("记到别人名下" in ag_txt),
        "got": "已写明" if "记到别人名下" in ag_txt else "(没写)",
    })

    # ---- 源码级：第二轮（2026-10-13）「对账依据必须交上来」----
    #   上面那三条硬规矩管住了"怎么读"；这一轮管的是"读得对不对**得有人能核**"。
    #   事故里助手认错一味 + 漏一味、总数还是 8 —— 因为压根没人对过账。
    for kw in ("sheets", "amount", "sum", "按张"):
        data["cases"].append({
            "name": "助手说明里写清了「对账依据」：%s" % kw,
            "pass": kw in ag_txt,
            "got": "有" if kw in ag_txt else "(缺)",
        })
    data["cases"].append({
        "name": "说明里点明 sheets 是**必交**（交不上来页面会明说没人替你核过）",
        "pass": ("必交" in ag_txt) and ("没人替你核过" in ag_txt),
        "got": "已写明" if "必交" in ag_txt else "(没写)",
    })
    data["cases"].append({
        # 工具第一版就栽在这：patient 写成中文键（姓名/性别），页面 patient.name 取不到，
        # 弹窗顶上照样打印"底方上没写姓名" —— 数据传了，键名不对，等于没传。
        "name": "说明里提醒 patient 用英文键、别照抄中文抬头行",
        "pass": ("键名" in ag_txt) and ("英文" in ag_txt),
        "got": "已写明" if "键名" in ag_txt else "(没写)",
    })
    data["cases"].append({
        "name": "页面里有「对账」块与渲染函数（#rxAudit / rxAudit()）",
        "pass": ('id="rxAudit"' in src) and ("function rxAudit(" in src),
        "got": "有" if 'id="rxAudit"' in src else "(缺)",
    })
    data["cases"].append({
        # 缺依据时**沉默会被读成"没问题"** —— 那正是这次栽的地方，必须有话说
        "name": "缺对账依据时不许沉默（源码里挂着「没交对账依据」这条提示）",
        "pass": ("没交「对账依据」" in src) and ("没人替你对过" in src),
        "got": "有" if "没人替你对过" in src else "(缺)",
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
        for fn in ("README.md", "_check/test_rxcap.py", "_check/shot.py",
                   "_check/test_rx_local.py", "tools/rx_local/rx_local.py"):
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

    # ---- 跨实现：本地工具生成的 #rx= 链接，真页面解得开吗 ----
    data["cases"].extend(cross_check_link())

    # ---- README 的声明必须和事实对得上（2026-10-13 加）----
    #   ★ 为什么值得几条断言：README 就是本项目**对外的那张报表**。它写着「跑 228 项断言」
    #     「docs/ 里每张图都被引用」「项目结构里列的文件都在」—— 而**没人会自己去核第二遍**。
    #     这正是本机那条反复出现的教训（见 git-sync §7.13/§7.16/§7.17）：
    #     **报告和事实之间少一道校验，它就迟早会说反话，而且失效时不响。**
    #     2026-10-13 人工排查时真对出两处：`docs/screenshot-empty.png` 提交了却没被引用，
    #     项目结构里也没列 `LICENSE`（别人 clone 下来一眼确认授权的地方）。
    proj = os.path.dirname(here)
    rd = ""
    rd_path = os.path.join(proj, "README.md")
    if os.path.exists(rd_path):
        with open(rd_path, encoding="utf-8") as fh:
            rd = fh.read()

    # (a) 截图：README 引用的必须存在；docs/ 里有的必须被引用（不许有没人看的孤儿图）
    # 文件名允许带数字：手机版那三张叫 m / mtab2 / mmore，
    # 原来这里只写 [a-z-]，mtab2 直接被漏掉，报成"没人引用的孤儿图"。
    ref = set(re.findall(r"docs/(screenshot-[a-z0-9-]+\.png)", rd))
    docs_dir = os.path.join(proj, "docs")
    have = set()
    if os.path.isdir(docs_dir):
        have = {f for f in os.listdir(docs_dir)
                if f.startswith("screenshot-") and f.endswith(".png")}
    _miss = sorted(ref - have)
    _orph = sorted(have - ref)
    data["cases"].append({
        "name": "README 引用的截图都存在，且 docs/ 里没有孤儿图（引 %d / 有 %d）"
                % (len(ref), len(have)),
        "pass": not _miss and not _orph,
        "got": ("；".join((["引用但不存在：" + "、".join(_miss)] if _miss else [])
                          + (["没人引用：" + "、".join(_orph)] if _orph else []))) or "0",
    })

    # (b) 「项目结构」那块里列的每一项都真的存在
    seg = re.search(r"##\s*项目结构\s*```(.*?)```", rd, flags=re.S)
    toks = []
    for ln in (seg.group(1) if seg else "").splitlines():
        tok = ln.strip().split("  ")[0].strip()
        if tok and not tok.startswith("#"):
            toks.append(tok)
    _bad = [t for t in toks if not os.path.exists(os.path.join(proj, t))]
    data["cases"].append({
        "name": "README「项目结构」里列的每一项都真的存在（%d 项）" % len(toks),
        "pass": bool(toks) and not _bad,
        "got": "、".join(_bad) if _bad else "0",
    })

    # (c) README 里写的断言条数 == 本脚本实际跑出来的条数
    #     ⚠️ 这里有个自指：本命断言自己也算一条，所以预期值 = 已累积条数 + 1。
    #     有人加了断言却忘了改 README，这条就会红 —— 那正是它存在的意义。
    _n = len(data["cases"]) + 1
    claims = set(re.findall(r"(\d+)\s*(?:项|条)\s*断言", rd)) | \
        set(re.findall(r"网页本体：(\d+)\s*项", rd))
    data["cases"].append({
        "name": "README 写的断言条数 == 实际（说 %s，实际 %d）"
                % ("/".join(sorted(claims)) or "?", _n),
        "pass": bool(claims) and {str(_n)} == claims,
        "got": "一致" if claims == {str(_n)} else "对不上——改 README 里的数字",
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
