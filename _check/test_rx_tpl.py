# -*- coding: utf-8 -*-
"""
drug-keeper：底方录入的「提示词」这条路 —— 真实 DOM 驱动测试。

用户 2026-10-23 的要求，原话：
    「底方录入那里 你设计一套提示词 让 ai 软件能够输出结构化的结果 比如说 让 ai 在药品信息
      前后加上特定的字符 现在 ai 出的是这样的……没有结构化 咱们这个系统可能出问题
      然后 提示词支持一键复制」

★ 2026-10-27 追加（推翻了 10-23 那条「顺带自动建药盒」）：
    「底方录入里 提示词不涉及药是什么时候吃的 程序也不用自动安排药什么时候吃
      这个建药盒 放药的过程完全交给用户 目的是用这一步来强制用户查清楚录入的药品对不对
      但是你不用把这个目的写在网站上」

所以现在这一块只钉**一件事**：把药名和用量读准、摆给人核对。
「什么时候吃」既不进提示词、也不进数据结构、更不碰药盒 —— 底方那一步的产出
就是「几味药 + 各自够吃多少天」，剩下的全由用户自己拖。

拆成五块：

① **提示词本身**：必须含 [药单开始]/[药单结束] 这对标记、含 7 个位置的写法、
   **不许**出现任何服药时间（七个时段一个都不能有）。按钮点一下要真进剪贴板。
② **切段**：AI 回的全文（寒暄 + 药单 + 用药提醒）只取药单中间那段。
   不切的话，寒暄那句「好的，我从照片上认出了 5 种药：」会被当成一味药解析。
③ **七个位置逐个认**：药名 / 商品名 / 规格 / 开几盒 / 每次用量 / 频次 / 用法。
④ **AI 原来的写法也不能崩**：它自己发挥的那版（「40mg×28片/盒，1盒 — 每次 40mg」）
   页面仍然要出 5 味药 —— 认不全是启发式的老问题，但不许整段解析失败。
⑤ **服药时间只擦不解析**：底方末尾带「早饭后」「睡前」时，这些字要从行里擦掉
   （不擦的话「睡前」会被频次正则当成「每晚一次」，把「每日2次」算成 1 次），
   但**不许**留下任何东西 —— 行对象上没有 slots，入库后药盒一格都不动。
   `#rx=` 链接那条路带了 slots 也一样被无视。

★ 全部用**虚构药名**（样例甲片/样例乙凝胶贴膏…）。真实处方上的药名在本机的
  `_check/_tmp_deny_names.txt` 里，`test_rxcap.py` 会扫源码，写进来会直接判失败。

做法同 test_box.py：复制 index.html，在主 IIFE 末尾注入探针，无头 Chrome --dump-dom 取回。

    python _check/test_rx_tpl.py
"""
import html
import json
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = drug-keeper/
SRC = os.path.join(ROOT, "index.html")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# AI 按提示词回的**完整一段**：寒暄 + 药单 + 药单外的提醒。
# 寒暄故意不用「金额/诊断」这类词 —— 那些本来就会被 RX_NOT_DRUG 滤掉，
# 用它们测「切段」等于白测（滤网挡住了，看不出切段有没有生效）。
# ★ 这几行末尾**故意还带着「早饭后」「睡前」「早中晚」** —— 提示词已经不要求了，
#   但 AI 常常自作主张加一句，用户粘过来也会带上。这些字必须被擦掉（它们会污染频次），
#   同时不许留下任何东西（不产出 slots、不碰药盒）。
AI_REPLY = """好的，我从照片上认出了 5 种药：
[药单开始]
样例甲片(某厂)[40mg*28片] 1盒 40mg 每日一次 口服 早饭后
样例乙凝胶贴膏[40mg*6贴] 5盒 1贴 每日2次 外用 早饭后+晚饭后
样例丙胶囊(某牌)[80mg*28粒] 2盒 160mg 每日一次 口服 睡前
样例丁注射液[100mg/1mL] 1支 100mg 每两周一次 皮下注射
样例戊片[600mg*30片/瓶] 1瓶 600mg 每日3次 口服 早中晚
[药单结束]

几点提醒：
样例丁是每两周打一次，建议固定日期并冷藏保存。
样例乙是疼的时候备着用的，别自己停样例甲。
"""

# 没写标记的手打文字（老用法）：不许因为「没有标记」就整段不认
HAND_TYPED = """样例己片(某厂)[0.25g*8] 2盒 0.5g 每日2次 口服
样例庚胶囊[10mg*30] 1瓶 10mg 每日一次 口服"""

# 用户贴过来的、AI 自己发挥的那一版（实测这一版解析得很难看，但不许崩）
AI_FREE = """样例甲片［某厂］40mg×28片/盒，1盒 — 每次 40mg，每日一次，口服
样例乙凝胶贴膏 40mg/贴×6贴/盒，5盒 — 每次 1贴，每日2次，外用
样例丙胶囊［某牌］80mg×28粒/盒，2盒 — 每次 160mg，每日一次，口服
样例丁注射液［某牌2］1mL:140mg，1支 — 每次 140mg，每两周一次，皮下注射
样例戊片（Ⅰ）［某牌3］600mg×30片/瓶，1瓶 — 每次 600mg，每日一次，口服"""

PROBE = r"""
(async function(){
  var R = { cases: [], asks: [] };
  var DAT = __DAT__;
  function q(s){ return document.querySelector(s); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function tick(){ return new Promise(function(r){ setTimeout(r, 0); }); }

  function tapAsk(btn){
    var d = q('#dlgAsk');
    if(!d || !d.open) return false;
    var btns = Array.prototype.slice.call(d.querySelectorAll('#askFoot .btn'));
    var pick = null, i;
    for(i = 0; i < btns.length; i++){
      if(btns[i].textContent.indexOf(btn) >= 0){ pick = btns[i]; break; }
    }
    if(!pick) return false;
    pick.click();
    return true;
  }
  function askInfo(){
    var d = q('#dlgAsk');
    if(!d || !d.open) return null;
    return { title: (q('#askTitle') || {}).textContent || '',
             sub: (q('#askSub') || {}).textContent || '' };
  }
  /* 把 submitRx 这一路上冒出来的弹窗依次点掉，并记下每一次的文案。
     list 是要点的按钮文案（按出现顺序）；点空了就停，免得死循环。 */
  async function settle(p, list){
    var got = [], finished = false;
    list = (list || []).slice();
    p.then(function(){ finished = true; }, function(){ finished = true; });
    for(var g = 0; g < 80 && !finished; g++){
      await tick();
      if(finished) break;
      var info = askInfo();
      if(!info) continue;
      if(!list.length) break;              // 弹窗冒出来了但没给按钮 → 停下来，别绕圈
      got.push(info);
      if(!tapAsk(list.shift())) break;
    }
    try { await p; } catch(e){ got.push({ title: '(!)抛异常', sub: String(e) }); }
    return got;
  }

  try {
    /* ============ ① 提示词本身 ============ */
    ck('提示词是一段非空文本', typeof RX_PROMPT === 'string' && RX_PROMPT.length > 200,
       (RX_PROMPT || '').length + ' 字');
    ck('提示词里给了起止标记 [药单开始] / [药单结束]',
       RX_PROMPT.indexOf('[药单开始]') >= 0 && RX_PROMPT.indexOf('[药单结束]') >= 0);
    ck('提示词写明了「这两行原样保留，程序靠它们找药单」',
       RX_PROMPT.indexOf('原样保留') >= 0 && RX_PROMPT.indexOf('程序靠它们') >= 0);
    ck('提示词含 7 个位置：药名 / 商品名 / 规格 / 开几盒 / 每次用量 / 频次 / 用法',
       ['药名', '商品名', '规格', '开几盒', '每次用量', '频次', '用法']
         .every(function(k){ return RX_PROMPT.indexOf(k) >= 0; }));
    ck('★ 提示词里**没有**第 8 个位置「什么时候吃」（2026-10-27 砍掉）',
       RX_PROMPT.indexOf('什么时候吃') < 0 && RX_PROMPT.indexOf('8 什么时候') < 0,
       RX_PROMPT.split('\n').filter(function(x){ return x.indexOf('什么时候吃') >= 0; }).join(' / '));
    ck('★ 提示词里一个服药时段都不出现（早饭前…睡前）',
       ['早饭前', '早饭后', '午饭前', '午饭后', '晚饭前', '晚饭后', '睡前']
         .every(function(k){ return RX_PROMPT.indexOf(k) < 0; }),
       ['早饭前', '早饭后', '午饭前', '午饭后', '晚饭前', '晚饭后', '睡前']
         .filter(function(k){ return RX_PROMPT.indexOf(k) >= 0; }).join('/'));
    ck('★ 提示词明说了不用写服药时间、不用算天数',
       /不用写[^\n]*服药时间/.test(RX_PROMPT) && RX_PROMPT.indexOf('不用算') >= 0,
       RX_PROMPT.split('\n').filter(function(x){ return /不用写|不用算/.test(x); }).join(' / '));
    ck('提示词的示例行只有 7 个位置（示例段里没有时段词）',
       (function(){
         var m = RX_PROMPT.match(/\[药单开始\]([\s\S]*?)\[药单结束\]/);
         if(!m) return false;
         return ['早饭前', '早饭后', '午饭前', '午饭后', '晚饭前', '晚饭后', '睡前']
           .every(function(k){ return m[1].indexOf(k) < 0; });
       })());
    ck('提示词要求看不清就写 ?、不许猜', RX_PROMPT.indexOf('不要猜') >= 0);
    ck('提示词允许在药单之外写提醒（那些不该进药单）',
       RX_PROMPT.indexOf('不要写进药单里') >= 0);
    ck('提示词的例子用的是占位药名（示例片），不是真底方上的药名',
       RX_PROMPT.indexOf('示例片') >= 0 && RX_PROMPT.indexOf('换成照片上真实的') >= 0);

    /* 复制按钮：真点一下，看剪贴板拿到的是不是完整提示词 */
    var copied = null;
    try {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: function(t){ copied = t; return Promise.resolve(); } }
      });
    } catch(e){}
    var tplBtn = q('#btnRxTpl');
    ck('底方录入弹窗里有「复制提示词」按钮', !!tplBtn);
    if(tplBtn){
      tplBtn.click();
      await tick();
      ck('★ 点一下真把提示词写进了剪贴板（不是只改了个文案）',
         copied === RX_PROMPT, (copied ? '长度为 ' + copied.length : '(剪贴板空)'));
      ck('复制后按钮变成「已复制 ✓」', tplBtn.textContent.indexOf('已复制') >= 0, tplBtn.textContent);
      await new Promise(function(r){ setTimeout(r, 1600); });
      ck('1.4 秒后按钮回到「复制提示词」（连点两次也不会卡在已复制）',
         tplBtn.textContent === '复制提示词', tplBtn.textContent);
    }

    /* ============ ② 切段 ============ */
    var got = rxParseText(DAT.ai);
    ck('AI 回的整段（寒暄 + 药单 + 提醒）只解析出 5 味药',
       got.length === 5, got.length + ' 味：' + got.map(function(r){ return r.name; }).join('/'));
    ck('寒暄那句「我从照片上认出了 5 种药」没被当成一味药',
       !got.some(function(r){ return /认出了|好的/.test(r.name); }));
    ck('药单外的提醒（「每两周打一次…冷藏保存」）没被当成药',
       !got.some(function(r){ return /冷藏|提醒/.test(r.name); }));
    ck('5 味的药名逐字对',
       got.map(function(r){ return r.name; }).join('|')
         === '样例甲片|样例乙凝胶贴膏|样例丙胶囊|样例丁注射液|样例戊片',
       got.map(function(r){ return r.name; }).join('|'));

    var noEnd = rxCutDrugBlock('[药单开始]\n样例甲片[40mg*28片] 1盒 40mg 每日一次 口服 早饭后');
    ck('只写了开始、忘了结束 → 取到末尾（不整段丢）',
       noEnd.indexOf('样例甲片') >= 0 && noEnd.indexOf('[药单开始]') < 0, noEnd.slice(0, 24));
    var full = rxCutDrugBlock('【药单开始】\n样例甲片[40mg*28片] 1盒\n【药单结束】\n后面的话');
    ck('【全角括号】的标记也认', full.indexOf('样例甲片') >= 0 && full.indexOf('后面的话') < 0);
    var bold = rxCutDrugBlock('**[药单开始]**\n样例甲片[40mg*28片] 1盒\n**[药单结束]**');
    ck('AI 加粗成 **[药单开始]** 也认', bold.indexOf('样例甲片') >= 0, bold.slice(0, 24));
    ck('完全没有标记（手打的）→ 整段照旧逐行猜，不是返回空',
       rxParseText(DAT.typed).length === 2, rxParseText(DAT.typed).length + ' 味');

    /* ============ ③ 八个位置逐个认 ============ */
    var a = got[0];
    ck('药名去掉商品名和规格', a && a.name === '样例甲片', a && a.name);
    ck('规格认出来了', a && /40mg\*28片/.test(a.spec), a && a.spec);
    ck('单位是「片」（没被规格里的「/盒」污染）', a && a.unit === '片', a && a.unit);
    ck('每次用量 = 40mg 的 1 片', a && a.perDose === 1, a && a.perDose);
    ck('频次 = 每日 1 次', a && a.cycleDays === 1 && a.timesPerCycle === 1,
       a && (a.cycleDays + '天×' + a.timesPerCycle));
    ck('本次开到 = 1 盒 × 28 片 = 28', a && a.total === 28, a && a.total);
    ck('够吃 28 天', a && rxDaysOf(a) === 28, a && rxDaysOf(a));

    var b = got[1];
    ck('外用写进了备注', b && b.note === '外用', b && b.note);
    ck('每日 2 次认对了', b && b.timesPerCycle === 2, b && b.timesPerCycle);
    ck('5 盒 × 6 贴 = 30 贴', b && b.total === 30, b && b.total);
    ck('够吃 15 天', b && rxDaysOf(b) === 15, b && rxDaysOf(b));

    var c = got[2];
    ck('160mg ÷ 80mg = 每次 2 粒', c && c.perDose === 2, c && c.perDose);
    ck('2 盒 × 28 粒 = 56 粒', c && c.total === 56, c && c.total);

    var d = got[3];
    ck('每两周一次 → 周期 14 天、1 次', d && d.cycleDays === 14 && d.timesPerCycle === 1,
       d && (d.cycleDays + '天×' + d.timesPerCycle));
    ck('皮下注射写进了备注', d && d.note === '皮下注射', d && d.note);
    ck('注射液单位推断为「支」', d && d.unit === '支', d && d.unit);
    ck('1 支够吃 14 天', d && rxDaysOf(d) === 14, d && rxDaysOf(d));

    /* ============ ④ AI 自己发挥的那版：认不全可以，崩不行 ============ */
    var free = rxParseText(DAT.free);
    ck('AI 没按格式写的老版本，仍然解析出 5 味药（不整段失败）',
       free.length === 5, free.length + ' 味');
    ck('老版本的 5 个药名都对得上', free.every(function(r){ return /^样例/.test(r.name); }),
       free.map(function(r){ return r.name; }).join('/'));

    /* ============ ⑤ 服药时间：只擦掉，不解析 ============ */
    function row(line){ return rxParseLine(line); }
    function keysOf(line){ return Object.keys(row(line)).join(','); }
    ck('★ 行对象上根本没有 slots 这个字段（不是空数组，是压根没有）',
       keysOf('样例片[10mg*10片] 1盒 10mg 每日一次 口服 早饭后').indexOf('slots') < 0,
       keysOf('样例片[10mg*10片] 1盒 10mg 每日一次 口服 早饭后'));
    ck('★ 擦掉「早饭后」不影响别的字段：药名 / 用量 / 单位 / 总量 / 天数照旧',
       (function(){
         var r = row('样例片(某厂)[10mg*10片] 1盒 10mg 每日一次 口服 早饭后');
         return r.name === '样例片' && r.perDose === 1 && r.unit === '片'
             && r.total === 10 && rxDaysOf(r) === 10;
       })(), (function(){
         var r = row('样例片(某厂)[10mg*10片] 1盒 10mg 每日一次 口服 早饭后');
         return r.name + ' / ' + r.perDose + ' / ' + r.unit + ' / ' + r.total;
       })());
    ck('★ 「每日2次 口服 睡前」的频次仍是 2 —— 睡前没被当成「每晚一次」',
       (function(){ var r = row('样例片[10mg*10片] 1盒 10mg 每日2次 口服 睡前');
         return r.timesPerCycle === 2 && r.cycleDays === 1; })(),
       (function(){ var r = row('样例片[10mg*10片] 1盒 10mg 每日2次 口服 睡前');
         return r.cycleDays + '天×' + r.timesPerCycle + '次'; })());
    ck('「早中晚」一口气写三个的也擦干净，频次还是 3',
       (function(){ var r = row('样例片[10mg*10片] 1盒 10mg 每日3次 口服 早中晚');
         return r.timesPerCycle === 3 && r.name === '样例片'; })(),
       (function(){ var r = row('样例片[10mg*10片] 1盒 10mg 每日3次 口服 早中晚');
         return r.name + ' / ' + r.timesPerCycle + '次'; })());
    ck('「临睡前」「晨起」「空腹」这些更随意的写法也擦掉，不留进药名',
       row('样例片[10mg*10片] 1盒 10mg 每日一次 口服 临睡前').name === '样例片'
       && row('样例片[10mg*10片] 1盒 10mg 每日一次 口服 晨起').name === '样例片'
       && row('样例片[10mg*10片] 1盒 10mg 每日一次 空腹服用').name === '样例片');
    ck('★ 「隔日一次 早饭后」：周期还是 2 天（没被时段改成每天）',
       (function(){ var r = row('样例片[10mg*10片] 1盒 10mg 隔日一次 口服 早饭后');
         return r.cycleDays === 2; })(),
       (function(){ return row('样例片[10mg*10片] 1盒 10mg 隔日一次 口服 早饭后').cycleDays; })());
    ck('整行只有时段、没有药（「早饭后」单独一行）→ 直接丢掉，不出空药行',
       rxParseText('早饭后').length === 0, rxParseText('早饭后').length + ' 行');

    /* ============ ⑥ 入库不碰药盒 ============ */
    function fixtureBox(){
      var p = curPerson();
      p.stage = [];
      var m = { id: uid(), name: '原先盒里的片', spec: '5mg', perDose: 1, unit: '片',
                cycleDays: 1, timesPerCycle: 1, stockBase: 10, stockDate: todayStr(),
                rxMax: 0, kind: 'daily' };
      p.meds = [m];
      p.box = { slots: [{ name: '早', ids: [m.id] }, { name: '晚', ids: [m.id] }] };
      setMeds(p.meds);
      save(); render();
      return p;
    }
    async function submit(text, btns){
      rxRows = rxParseText(text);
      rxRows.forEach(function(r){ r.picked = true; });
      return await settle(submitRx({ preventDefault: function(){} }), btns);
    }
    function boxShape(){
      return curBox().slots.map(function(s){
        return s.name + ':' + s.ids.map(function(id){
          var m = meds.filter(function(x){ return x.id === id; })[0];
          return m ? m.name : '(野指针)';
        }).join('+');
      }).join('|');
    }

    fixtureBox();
    var bBefore = boxShape();
    var asks1 = await submit(DAT.ai, ['知道了']);
    var bAfter = boxShape();
    ck('入库只弹一个「已加入 N 味药」，不再问要不要重排药盒',
       asks1.length === 1 && asks1[0].title.indexOf('已加入') >= 0,
       asks1.map(function(x){ return x.title; }).join(' / '));
    ck('★ 药盒一格都没动（格数、格名、里面摆的是谁都原样）',
       bBefore === bAfter, bBefore + '  →  ' + bAfter);
    ck('★ 5 味药全都进了药箱（入库这一步不受影响）', meds.length === 6, meds.length + ' 味');
    ck('★ 刚入库的 5 味全在「未入清单」里，等着人自己摆',
       (function(){
         var un = unplacedMeds().map(function(m){ return m.name; });
         return ['样例甲片', '样例乙凝胶贴膏', '样例丙胶囊', '样例丁注射液', '样例戊片']
           .every(function(n){ return un.indexOf(n) >= 0; });
       })(), unplacedMeds().map(function(m){ return m.name; }).join('/'));
    ck('★ 入库提示里一个字都不提药盒（不暗示程序替人摆过）',
       asks1[0].sub.indexOf('药盒') < 0 && asks1[0].sub.indexOf('格') < 0,
       asks1[0].sub.slice(0, 70));

    // —— 空药盒也一样：不会因为「盒子正好空着」就顺手建几格
    fixtureBox();
    var p2 = curPerson();
    p2.meds = [];
    p2.box = { slots: defaultSlots(3) };
    setMeds(p2.meds); save(); render();
    var emptyBefore = boxShape();
    await submit(DAT.ai, ['知道了']);
    ck('★★ 空药盒也不建格：还是原来那 3 个空格，格名一个字没改',
       boxShape() === emptyBefore, emptyBefore + '  →  ' + boxShape());
    ck('空盒里那 3 格仍然是空的（药没被塞进去）',
       curBox().slots.every(function(s){ return s.ids.length === 0; }),
       curBox().slots.map(function(s){ return s.ids.length; }).join('/'));

    // —— `#rx=` 链接那条路：带了 slots 也一律无视
    //   两条路（粘文字 / 链接）现在都**不再**处理服药时间。这里钉住链接那条：
    //   AI 自作主张塞 slots 进来，不许在界面上冒出来，也不许入库后去动药盒。
    fixtureBox();
    await openRx([
      { name:'样例己片', spec:'10mg*24片', unit:'片', perDose:1,
        cycleDays:1, timesPerCycle:2, total:48, slots:['午饭前', '睡前'] },
      { name:'样例庚胶囊', spec:'0.25g*12粒', unit:'粒', perDose:1,
        cycleDays:1, timesPerCycle:1, total:24, slots:['傍晚'] }
    ], 'AI');
    ck('链接里的 slots 不在行上显示（页面上没有任何「摆进药盒」的提示）',
       document.querySelectorAll('#rxList .rx-slotline').length === 0,
       document.querySelectorAll('#rxList .rx-slotline').length + ' 处');
    ck('链接进来的行也没有 slots 字段',
       !('slots' in rxRows[0]) && !('slots' in rxRows[1]),
       Object.keys(rxRows[0] || {}).join(','));
    var b5Before = boxShape();
    await settle(submitRx({ preventDefault: function(){} }), ['知道了']);
    ck('★★ 链接那条路入库后药盒同样一格不动',
       boxShape() === b5Before, b5Before + '  →  ' + boxShape());
    ck('两味药都进药箱了',
       meds.some(function(m){ return m.name === '样例己片'; })
       && meds.some(function(m){ return m.name === '样例庚胶囊'; }));

  } catch(err){
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
    dat = json.dumps({"ai": AI_REPLY, "typed": HAND_TYPED, "free": AI_FREE},
                     ensure_ascii=False)
    probe = PROBE.replace("__DAT__", dat)
    tmpdir = tempfile.mkdtemp(prefix="dk_tpl_")
    page = os.path.join(tmpdir, "t.html")
    open(page, "w", encoding="utf-8").write(src[:i] + probe + src[i:])

    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--allow-file-access-from-files",
           "--window-size=1280,1400", "--virtual-time-budget=12000",
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
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
