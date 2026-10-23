# -*- coding: utf-8 -*-
"""
drug-keeper：底方录入的「提示词」这条路 —— 真实 DOM 驱动测试。

用户 2026-10-23 的要求，原话：
    「底方录入那里 你设计一套提示词 让 ai 软件能够输出结构化的结果 比如说 让 ai 在药品信息
      前后加上特定的字符 现在 ai 出的是这样的……没有结构化 咱们这个系统可能出问题
      然后 提示词支持一键复制」
    「提示词里还需要告诉 ai 患者是早中晚饭前还是饭后吃 然后 根据这个 在录入药品的时候
      可以自动创建好药盒 把药物放进去」

拆成六块，本脚本就按这六块钉：

① **提示词本身**：必须含 [药单开始]/[药单结束] 这对标记、含 8 个位置的写法、
   含七个服药时段、含「不是每天吃的写 -」。按钮点一下要真进剪贴板。
② **切段**：AI 回的全文（寒暄 + 药单 + 用药提醒）只取药单中间那段。
   不切的话，寒暄那句「好的，我从照片上认出了 5 种药：」会被当成一味药解析。
③ **八个位置逐个认**：药名 / 商品名 / 规格 / 开几盒 / 每次用量 / 频次 / 用法 / 什么时候吃。
④ **AI 原来的写法也不能崩**：它自己发挥的那版（「40mg×28片/盒，1盒 — 每次 40mg」）
   页面仍然要出 5 味药 —— 认不全是启发式的老问题，但不许整段解析失败。
⑤ **时段 → 药盒**：格名就是七个标准时段、按标准顺序、一味药能进多格、
   隔两周/按需的那种**不进盒**（塞进每日药盒就是撒谎）。
⑥ **建盒不硬来**：药盒里有药 → 先问；选「不动」→ 盒子一格都不动。

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
AI_REPLY = """好的，我从照片上认出了 5 种药：
[药单开始]
样例甲片(某厂)[40mg*28片] 1盒 40mg 每日一次 口服 早饭后
样例乙凝胶贴膏[40mg*6贴] 5盒 1贴 每日2次 外用 早饭后+晚饭后
样例丙胶囊(某牌)[80mg*28粒] 2盒 160mg 每日一次 口服 睡前
样例丁注射液[100mg/1mL] 1支 100mg 每两周一次 皮下注射 -
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
    ck('提示词含 8 个位置：药名 / 商品名 / 规格 / 开几盒 / 每次用量 / 频次 / 用法 / 什么时候吃',
       ['药名', '商品名', '规格', '开几盒', '每次用量', '频次', '用法', '什么时候吃']
         .every(function(k){ return RX_PROMPT.indexOf(k) >= 0; }));
    ck('提示词把七个时段全列出来了',
       ['早饭前', '早饭后', '午饭前', '午饭后', '晚饭前', '晚饭后', '睡前']
         .every(function(k){ return RX_PROMPT.indexOf(k) >= 0; }));
    ck('提示词说明了一天吃几次就写几个（+ 连起来）', RX_PROMPT.indexOf('+ 连起来') >= 0);
    ck('提示词说明了「不是每天吃的写 -」', /不是每天吃的[^\n]*-/.test(RX_PROMPT));
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

    /* ============ ⑤ 时段 ============ */
    function slots(line){ return rxParseLine(line).slots.join('+'); }
    ck('「早饭后」→ 早饭后', slots('样例片[10mg*10片] 1盒 10mg 每日一次 口服 早饭后') === '早饭后');
    ck('「早饭前」和「早饭后」是两个不同的格子（前 ≠ 后）',
       slots('样例片[10mg*10片] 1盒 10mg 每日一次 口服 早饭前') === '早饭前');
    ck('早饭后+晚饭后 → 两个格，按标准顺序排',
       slots('样例片[10mg*10片] 1盒 10mg 每日2次 口服 早饭后+晚饭后') === '早饭后+晚饭后');
    ck('写反了顺序也按标准顺序返回（晚饭后+早饭后 仍是 早饭后+晚饭后）',
       slots('样例片[10mg*10片] 1盒 10mg 每日2次 口服 晚饭后+早饭后') === '早饭后+晚饭后');
    ck('「早中晚」一口气写三个 → 三格都认（按饭后算）',
       slots('样例片[10mg*10片] 1盒 10mg 每日3次 口服 早中晚') === '早饭后+午饭后+晚饭后');
    ck('「睡前」单独一格', slots('样例片[10mg*10片] 1盒 10mg 每日一次 口服 睡前') === '睡前');
    ck('「临睡前」也算睡前', slots('样例片[10mg*10片] 1盒 10mg 每日一次 口服 临睡前') === '睡前');
    ck('「早餐前 / 午餐后 / 晚餐后」这些近义写法都归到标准格',
       slots('样例片[10mg*10片] 1盒 10mg 每日3次 口服 早餐前、午餐后、晚餐后')
         === '早饭前+午饭后+晚饭后');
    ck('「-」（不是每天吃的）→ 不进盒', slots('样例针[10mg] 1支 10mg 每两周一次 皮下注射 -') === '');
    ck('照片上没写时段的（如隔天一次）→ 不进盒，不瞎猜一格',
       slots('样例片[10mg*10片] 1盒 10mg 隔日一次 口服') === '');
    ck('七个标准之外的写法（「晨起」）→ 丢掉，不造出第八个格子',
       slots('样例片[10mg*10片] 1盒 10mg 每日一次 口服 晨起') === '');
    ck('★ 时段先摘再解析：「每日2次 睡前服」的频次仍是 2（没被「睡前」改写成 1 次）',
       (function(){
         var r = rxParseLine('样例片[10mg*10片] 1盒 10mg 每日2次 口服 睡前');
         return r.timesPerCycle === 2 && r.slots.join('+') === '睡前';
       })(), (function(){
         var r = rxParseLine('样例片[10mg*10片] 1盒 10mg 每日2次 口服 睡前');
         return r.timesPerCycle + '次 / ' + r.slots.join('+');
       })());
    ck('★ 「隔日一次 早饭后」这种：时段认出来、但周期是 2 天（不改成每天）',
       (function(){
         var r = rxParseLine('样例片[10mg*10片] 1盒 10mg 隔日一次 口服 早饭后');
         return r.cycleDays === 2 && r.slots.join('+') === '早饭后';
       })());

    /* ============ ⑥ 自动建药盒 ============ */
    function fixtureBox(withOld){
      var p = curPerson();
      p.stage = [];
      if(withOld){
        var m = { id: uid(), name: '原先盒里的片', spec: '5mg', perDose: 1, unit: '片',
                  cycleDays: 1, timesPerCycle: 1, stockBase: 10, stockDate: todayStr(),
                  rxMax: 0, kind: 'daily' };
        p.meds = [m];
        p.box = { slots: [{ name: '早', ids: [m.id] }, { name: '晚', ids: [m.id] }] };
      } else {
        p.meds = [];
        p.box = { slots: defaultSlots(3) };
      }
      setMeds(p.meds);
      save(); render();
      return p;
    }
    async function submit(text, btns){
      rxRows = rxParseText(text);
      rxRows.forEach(function(r){ r.picked = true; });
      return await settle(submitRx({ preventDefault: function(){} }), btns);
    }

    // —— 场景一：药盒本来是空的（三个空格）→ 不问，直接摆
    fixtureBox(false);
    var asks1 = await submit(DAT.ai, ['知道了']);
    var box = curBox();
    var names = box.slots.map(function(s){ return s.name; });
    ck('药盒是空的时候不弹确认框（没什么可覆盖的，不拿这个烦人）',
       asks1.length === 1 && asks1[0].title.indexOf('已加入') >= 0,
       asks1.map(function(x){ return x.title; }).join(' / '));
    ck('★ 按用到的时段建格：早饭后 / 午饭后 / 晚饭后 / 睡前，共 4 格',
       names.join('|') === '早饭后|午饭后|晚饭后|睡前', names.join('|'));
    ck('没有为了整齐补出「早饭前」「晚饭前」这些空格子', names.length === 4, names.length + ' 格');

    function idsOf(name){
      var s = box.slots.filter(function(x){ return x.name === name; })[0];
      return s ? s.ids.map(function(id){
        var m = meds.filter(function(x){ return x.id === id; })[0];
        return m ? m.name : '(野指针)';
      }) : [];
    }
    ck('「样例甲片」在早饭后格',
       idsOf('早饭后').indexOf('样例甲片') >= 0, idsOf('早饭后').join('/'));
    ck('★ 一天两次的「样例乙凝胶贴膏」同时进了早饭后和晚饭后两格',
       idsOf('早饭后').indexOf('样例乙凝胶贴膏') >= 0
       && idsOf('晚饭后').indexOf('样例乙凝胶贴膏') >= 0,
       idsOf('早饭后').join('/') + ' || ' + idsOf('晚饭后').join('/'));
    ck('「样例丙胶囊」在睡前格', idsOf('睡前').indexOf('样例丙胶囊') >= 0, idsOf('睡前').join('/'));
    ck('「样例戊片」（早中晚）三格都有它',
       ['早饭后', '午饭后', '晚饭后'].every(function(n){ return idsOf(n).indexOf('样例戊片') >= 0; }));
    ck('★ 每两周打一次的「样例丁注射液」不进任何一格（每日药盒里放它 = 撒谎）',
       box.slots.every(function(s){ return idsOf(s.name).indexOf('样例丁注射液') < 0; }));
    ck('但它照样在药箱里（药本身入库了，只是没摆进格子）',
       meds.some(function(m){ return m.name === '样例丁注射液'; }));
    ck('入库的药一共有 5 味', meds.length === 5, meds.length + ' 味');
    ck('提示里说清了「药盒也照什么时候吃摆好了」',
       asks1[0].sub.indexOf('药盒也照') >= 0 && asks1[0].sub.indexOf('4 格') >= 0,
       asks1[0].sub.slice(0, 60));

    // —— 场景二：药盒里本来有药 → 先问；选「不动」→ 一格都不动
    fixtureBox(true);
    var before = curBox().slots.map(function(s){ return s.name + ':' + s.ids.length; }).join('|');
    var asks2 = await submit(DAT.ai, ['取消', '知道了']);
    var after = curBox().slots.map(function(s){ return s.name + ':' + s.ids.length; }).join('|');
    ck('★ 药盒里有药时先弹确认，标题带「重排药盒」',
       asks2.length >= 1 && asks2[0].title.indexOf('重排药盒') >= 0,
       asks2.map(function(x){ return x.title; }).join(' / '));
    ck('确认框说清了现在盒里有几格几份药（覆盖后果）',
       asks2.length >= 1 && /2 格/.test(asks2[0].sub) && /2 份/.test(asks2[0].sub),
       asks2.length ? asks2[0].sub.slice(0, 70) : '');
    ck('选「取消 / 不动」→ 药盒一格没变', before === after, before + ' → ' + after);
    ck('选「不动」之后，5 味药照样入库了', meds.length === 6, meds.length + ' 味');
    ck('提示里说明了「药盒没动」',
       asks2.length > 1 && asks2[1].sub.indexOf('药盒没动') >= 0,
       asks2.length > 1 ? asks2[1].sub.slice(0, 50) : '');

    // —— 场景三：药盒里有药，选「重排」→ 整个换掉
    fixtureBox(true);
    var asks3 = await submit(DAT.ai, ['重排药盒', '知道了']);
    var box3 = curBox();
    ck('点「重排药盒」→ 格子换成新的 4 格',
       box3.slots.map(function(s){ return s.name; }).join('|') === '早饭后|午饭后|晚饭后|睡前',
       box3.slots.map(function(s){ return s.name; }).join('|'));
    ck('原来盒里那位「原先盒里的片」已经不在盒里',
       box3.slots.every(function(s){ return s.ids.length === 0
         || s.ids.every(function(id){
              var m = meds.filter(function(x){ return x.id === id; })[0];
              return m && m.name !== '原先盒里的片';
            }); }));
    ck('但它还在药箱里（只是没摆进格子）',
       meds.some(function(m){ return m.name === '原先盒里的片'; }));

    // —— 场景四：AI 直接给 `#rx=` 链接那条路，rows 里也带 slots
    //   这条容易漏：粘文字那条路走 rxParseLine，链接那条路是 openRx(rxNewRow(x))，
    //   两边的清洗规则必须一样（七档之外的一律丢掉），否则 AI 写「傍晚」就会造出第八个格子。
    fixtureBox(false);
    await openRx([
      { name:'样例己片', spec:'10mg*24片', unit:'片', perDose:1,
        cycleDays:1, timesPerCycle:2, total:48, slots:['午饭前', '睡前'] },
      { name:'样例庚胶囊', spec:'0.25g*12粒', unit:'粒', perDose:1,
        cycleDays:1, timesPerCycle:1, total:24, slots:['傍晚'] }     // 不在七档里，应被丢掉
    ], 'AI');
    var subTxt = Array.prototype.slice.call(document.querySelectorAll('#rxList .rx-slotline'))
      .map(function(e){ return e.textContent; }).join(' || ');
    ck('链接里的时段也显示在行上',
       subTxt.indexOf('午饭前') >= 0 && subTxt.indexOf('睡前') >= 0, subTxt);
    ck('七档之外的「傍晚」被丢掉，不留到下一层',
       subTxt.indexOf('傍晚') < 0, subTxt);

    var asks4 = await settle(submitRx({ preventDefault: function(){} }), ['知道了']);
    var box4 = curBox();
    ck('★ 链接那条路入库后也照样建盒：只有 午饭前 / 睡前 两格',
       box4.slots.map(function(s){ return s.name; }).join('|') === '午饭前|睡前',
       box4.slots.map(function(s){ return s.name; }).join('|'));
    ck('这味两顿的药在两格里都有它',
       box4.slots.every(function(s){
         var names = s.ids.map(function(id){
           var m = meds.filter(function(x){ return x.id === id; })[0];
           return m ? m.name : '(野指针)';
         });
         return names.indexOf('样例己片') >= 0;
       }), box4.slots.map(function(s){ return s.ids.length; }).join('/'));

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
