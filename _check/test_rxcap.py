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
  var R = { alerts: [], cases: [] };
  window.__T = R;
  window.alert = function(m){ R.alerts.push(String(m)); };
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

  // --- 7b. 弹窗预览 ---
  openBulk();
  var brows = document.querySelectorAll('#bulkList .disp-row');
  ck('弹窗列出全部 6 味（含跳过的那味）', brows.length === 6, brows.length);
  ck('标题写清作用对象是当前人', /「我」药箱/.test(q('#bulkSub').textContent), q('#bulkSub').textContent);
  var body = q('#bulkList').textContent.replace(/\s+/g, ' ');
  ck('预览同时给出加/减两个方向的数', /＋7 天 → 8 片/.test(body) && /−7 天/.test(body), body.slice(0, 200));
  ck('预览标出会被减到 0 的药', /已减到 0/.test(body), '');
  ck('预览标出没设用法的药', /没法按天算/.test(body), '');
  ck('按钮带上了天数', /全部增加 7 天/.test(q('#btnBulkPlus').textContent)
     && /全部减少 7 天/.test(q('#btnBulkMinus').textContent),
     q('#btnBulkPlus').textContent);
  q('#bulkDays').value = 14; renderBulk();
  ck('改天数 → 按钮与预览跟着变', /全部增加 14 天/.test(q('#btnBulkPlus').textContent),
     q('#btnBulkPlus').textContent);
  q('#bulkDays').value = 7; renderBulk();

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
  openBulk(); q('#bulkDays').value = 100; renderBulk();
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
