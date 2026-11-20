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

① **提示词本身**：必须含 [药单开始]/[药单结束] 这对标记、含 7 个**字段名**的写法、
   **不许**出现任何服药时间（七个时段一个都不能有）。按钮点一下要真进剪贴板。
   ★ 2026-11-20 用户改口径，原话：「0.25ug36粒 中间少了一个 * …… 你修正一下提示词
     让 ai 能生成结构化的回复 药品名称 / 单位计量 / 每盒数量 / 盒数 / 服用计量
     总之就是做成文本化的 json 方便程序的读取」。
     所以提示词从「7 个位置、空格隔开」改成「7 个字段名、`字段名=值`、`|` 隔开」：
     位置靠数，字段名靠认 —— 少一个分隔符不至于把「每盒 36 粒」读成 1 粒。
② **切段**：AI 回的全文（寒暄 + 药单 + 用药提醒）只取药单中间那段。
   不切的话，寒暄那句「好的，我从照片上认出了 5 种药：」会被当成一味药解析。
③ **七个字段逐个认**：药品名称 / 单位剂量 / 每盒数量 / 盒数 / 每次用量 / 频次 / 用法。
④ **AI 原来的写法也不能崩**：它自己发挥的那版（「40mg×28片/盒，1盒 — 每次 40mg」）
   页面仍然要出 5 味药 —— 认不全是启发式的老问题，但不许整段解析失败。
⑤ **服药时间只擦不解析**：底方末尾带「早饭后」「睡前」时，这些字要从行里擦掉
   （不擦的话「睡前」会被频次正则当成「每晚一次」，把「每日2次」算成 1 次），
   但**不许**留下任何东西 —— 行对象上没有 slots，入库后药盒一格都不动。
   `#rx=` 链接那条路带了 slots 也一样被无视。
⑥ **字段化那版（2026-11-20）真的能读**：`字段名=值`、全角冒号、一行 JSON、
   整段 JSON（带 ``` 代码块）、漏字段、老的「少一个 *」写法 —— 各来一条。

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

# ★ 2026-11-20 用户实测栽住的那两条：AI 把 `*` 写丢了（[0.25ug36粒]）。
#   原来只认 `*N`，「每盒 36 粒」认不出来 → 本次开到 = 1 粒 → 余量 0 天。
#   这两条必须认对；而且**一个字段都不许挂提示**（数是自己算出来的，不是猜的）。
NO_STAR = """样例己胶囊[0.25ug36粒] 1盒 0.25μg 每日2次 口服
样例庚胶囊[0.43g30粒] 7盒 2.15g 每日3次 口服"""

# 字段化那版：AI 按新提示词回的（`字段名=值`，`|` 隔开）
AI_KV = """好的，按格式整理好了：
[药单开始]
药品名称=样例甲片 | 单位剂量=10mg | 每盒数量=24片 | 盒数=1 | 每次用量=10mg | 频次=每日一次 | 用法=口服
药品名称=样例乙凝胶贴膏 | 单位剂量=40mg | 每盒数量=6贴 | 盒数=5 | 每次用量=1贴 | 频次=每日2次 | 用法=外用
药品名称=样例丙胶囊 | 单位剂量=0.25g | 每盒数量=12粒 | 盒数=2 | 每次用量=0.5g | 频次=每日2次 | 用法=口服
[药单结束]
样例乙是疼的时候贴的。"""

# 同一份，换成全角标点 + 键不带「每」（AI 换了个叫法）
AI_KV2 = """[药单开始]
药名：样例甲片；单位剂量：10mg；每盒数量：24片；盒数：1；每次用量：10mg；频次：每日一次；用法：口服
[药单结束]"""

# 整段 JSON（还套了 ``` 代码块）—— 有的 AI 就爱这么回
AI_JSON = """```json
[
 {"药品名称":"样例甲片","单位剂量":"10mg","每盒数量":"24片","盒数":1,"每次用量":"10mg","频次":"每日一次","用法":"口服"},
 {"药品名称":"样例丙胶囊","单位剂量":"0.25g","每盒数量":"12粒","盒数":2,"每次用量":"0.5g","频次":"每日2次","用法":"口服"}
]
```"""


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
    ck('提示词要求这两行原样保留（现在只留一句，不再解释「程序靠它们」）',
       RX_PROMPT.indexOf('原样保留') >= 0);
    ck('提示词含 7 个字段名：药品名称 / 单位剂量 / 每盒数量 / 盒数 / 每次用量 / 频次 / 用法',
       ['药品名称', '单位剂量', '每盒数量', '盒数', '每次用量', '频次', '用法']
         .every(function(k){ return RX_PROMPT.indexOf(k) >= 0; }));
    ck('★ 提示词要求写成「字段名=值」、字段之间用 | 隔开（2026-11-20 从「靠位置」改成「靠名字」）',
       RX_PROMPT.indexOf('字段名=值') >= 0 && RX_PROMPT.indexOf('字段之间用 | 隔开') >= 0,
       RX_PROMPT.split('\n').filter(function(x){ return /字段名=值|隔开/.test(x); }).join(' / '));
    ck('★ 提示词写明了「数要和单位连着写、中间不要加 * ×」—— 就是这次栽的那个坑',
       RX_PROMPT.indexOf('中间不要加 * ×') >= 0);
    ck('★ 提示词是功能性写法：开场一句祈使句，通篇没有「为什么 / 因为」这类自我解释',
       /^把这张处方整理成下面的格式/.test(RX_PROMPT)
       && RX_PROMPT.indexOf('为什么') < 0 && RX_PROMPT.indexOf('因为') < 0,
       RX_PROMPT.split('\n')[0]);
    ck('★ 示例段是「1 行开方日期 + 3 行药」，药行各 7 个「=」（给 AI 对着抄的样板）',
       (function(){
         var m = RX_PROMPT.match(/\[药单开始\]([\s\S]*?)\[药单结束\]/);
         if(!m) return false;
         var lines = m[1].split('\n').filter(function(x){ return x.trim(); });
         return lines.length === 4
           && /^开方日期=\d{4}-\d{2}-\d{2}$/.test(lines[0].trim())
           && lines.slice(1).every(function(x){ return (x.match(/=/g) || []).length === 7; });
       })(), (function(){
         var m = RX_PROMPT.match(/\[药单开始\]([\s\S]*?)\[药单结束\]/);
         return m ? m[1].trim().split('\n').length + ' 行' : '(没有示例段)';
       })());

    ck('★ 提示词里**没有**第 8 个位置「什么时候吃」（2026-10-27 砍掉）',
       RX_PROMPT.indexOf('什么时候吃') < 0 && RX_PROMPT.indexOf('8 什么时候') < 0,
       RX_PROMPT.split('\n').filter(function(x){ return x.indexOf('什么时候吃') >= 0; }).join(' / '));
    ck('★ 提示词里一个服药时段都不出现（早饭前…睡前）',
       ['早饭前', '早饭后', '午饭前', '午饭后', '晚饭前', '晚饭后', '睡前']
         .every(function(k){ return RX_PROMPT.indexOf(k) < 0; }),
       ['早饭前', '早饭后', '午饭前', '午饭后', '晚饭前', '晚饭后', '睡前']
         .filter(function(k){ return RX_PROMPT.indexOf(k) >= 0; }).join('/'));
    ck('★ 提示词里同样不提「够吃几天」（那是页面自己算的）',
       RX_PROMPT.indexOf('够吃') < 0 && RX_PROMPT.indexOf('天数') < 0,
       RX_PROMPT.split('\n').filter(function(x){ return /够吃|天数/.test(x); }).join(' / '));
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
    ck('提示词的例子用的是占位药名（样例甲片），不是真底方上的药名',
       RX_PROMPT.indexOf('样例甲片') >= 0 && RX_PROMPT.indexOf('换成处方上真实的') >= 0);
    /* ★ 提示词和解析器必须是同一套写法 —— 否则人家照着抄了，页面还是读不懂。
       把整段提示词原样喂回解析器：示例那 3 味要一味不少、数要对。 */
    var ex = rxParseText(RX_PROMPT);
    ck('★ 把提示词里的示例段喂回解析器 → 认出 3 味（提示词与解析器是同一套写法）',
       ex.length === 3, ex.length + ' 味：' + ex.map(function(r){ return r.name; }).join('/'));
    ck('★ 示例段第一行的开方日期也捡出来了（不是当成第 4 味药）',
       ex.rxDate === '2026-03-08', ex.rxDate);
    ck('示例第一味：10mg*24片 1盒 → 24 片 / 够 24 天',
       ex[0] && ex[0].total === 24 && rxDaysOf(ex[0]) === 24,
       ex[0] && (ex[0].total + ' 片 / ' + rxDaysOf(ex[0]) + ' 天'));
    ck('示例第二味：0.25g*12粒 2盒 → 24 粒 / 够 12 天',
       ex[1] && ex[1].total === 24 && rxDaysOf(ex[1]) === 12,
       ex[1] && (ex[1].total + ' 粒 / ' + rxDaysOf(ex[1]) + ' 天'));
    ck('示例第三味：140mg 每两周一次 → 每 14 天 1 次、够 28 天',
       ex[2] && ex[2].cycleDays === 14 && rxDaysOf(ex[2]) === 28,
       ex[2] && (ex[2].cycleDays + ' 天 / ' + rxDaysOf(ex[2]) + ' 天'));


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
      // ★ 2026-11-20：用户截图指出按钮被折到第二行、且是白底描边的次要样式。
      //   这里守三件事：实心带色 / 跟标题同一行 / 下面不再挂一段解释。
      var _bcs = window.getComputedStyle(tplBtn);
      ck('★ 「复制提示词」是实心带色按钮（不再是白底描边的次要按钮）',
         !/rgba\(0, 0, 0, 0\)/.test(_bcs.backgroundColor) &&
         !/rgb\(255, 255, 255\)/.test(_bcs.backgroundColor), _bcs.backgroundColor);
      ck('第 1 步下面不再挂那段解释（用户要「直接按钮 + 提示词内容」）',
         !tplBtn.closest('.rx-step').querySelector('.rx-step-d'));
    }
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

    /* ============ ⑥ 字段化那版 + 「少一个 *」的老写法 ============ */
    // (a) 用户这次真栽的两条：AI 把规格里的 `*` 写丢了
    var ns = rxParseText(DAT.nostar);
    ck('★★ 规格少了 * 也认：[0.25ug36粒] → 每盒 36 粒，不是 1 粒',
       !!ns[0] && ns[0].total === 36, ns[0] && ns[0].total + ' 粒');
    ck('★ 0.25μg 里的微克符号（μg / µg）认得出来 → 每次 1 粒',
       !!ns[0] && ns[0].perDose === 1 && ns[0].unit === '粒',
       ns[0] && (ns[0].perDose + ' / ' + ns[0].unit));
    ck('★ 36 粒 ÷ 每天 2 次 → 够 18 天', !!ns[0] && rxDaysOf(ns[0]) === 18, ns[0] && rxDaysOf(ns[0]));
    ck('★ 这两条一个提示都不该挂（数是算出来的，不是猜的）',
       ns.length === 2 && ns.every(function(r){ return r.warn === ''; }),
       ns.map(function(r){ return r.warn; }).join('/') || '（空）');
    ck('★★ [0.43g30粒] 7盒 2.15g 每日3次 → 每次 5 粒、共 210 粒、够 14 天',
       !!ns[1] && ns[1].perDose === 5 && ns[1].total === 210 && rxDaysOf(ns[1]) === 14,
       ns[1] && (ns[1].perDose + ' 粒/次 · ' + ns[1].total + ' 粒 · ' + rxDaysOf(ns[1]) + ' 天'));

    // (b) 字段化那版（提示词让 AI 回的样子）
    var kv = rxParseText(DAT.kv);
    ck('字段化药单：寒暄那句不进药单，3 味全认出', kv.length === 3,
       kv.length + ' 味：' + kv.map(function(r){ return r.name; }).join('/'));
    ck('★ 药名干净（没把「药品名称」四个字和等号吞进名字里）',
       kv[0] && kv[0].name === '样例甲片', kv[0] && kv[0].name);
    ck('★ 规格还原成 10mg*24片（跟老格式长得一样，人好核对）',
       kv[0] && kv[0].spec === '10mg*24片', kv[0] && kv[0].spec);
    ck('1 盒 × 24 片 = 24 片、够 24 天',
       kv[0] && kv[0].total === 24 && rxDaysOf(kv[0]) === 24,
       kv[0] && (kv[0].total + ' 片 / ' + rxDaysOf(kv[0]) + ' 天'));
    ck('外用进备注、5 盒 × 6 贴 = 30 贴、够 15 天',
       kv[1] && kv[1].note === '外用' && kv[1].total === 30 && rxDaysOf(kv[1]) === 15,
       kv[1] && (kv[1].note + ' / ' + kv[1].total + ' / ' + rxDaysOf(kv[1])));
    ck('每次用量 0.5g ÷ 单位剂量 0.25g = 每次 2 粒',
       kv[2] && kv[2].perDose === 2, kv[2] && kv[2].perDose);

    // (c) 全角冒号 / 分号、键名换个叫法（AI 不会每次都叫「药品名称」）
    var kv2 = rxParseText(DAT.kv2);
    ck('全角冒号、分号隔开、键写成「药名」也认',
       kv2.length === 1 && kv2[0].name === '样例甲片' && kv2[0].total === 24,
       kv2.length + ' 味 / ' + (kv2[0] && (kv2[0].name + ' ' + kv2[0].total)));

    // (d) 有的 AI 直接甩一段 JSON（外面还套 ``` 代码块）
    var jj = rxParseText(DAT.json);
    ck('整段 JSON（带 ``` 代码块）也认，2 味', jj.length === 2, jj.length + ' 味');
    ck('JSON 那一版的数也对：24 片 / 够 24 天',
       jj[0] && jj[0].total === 24 && rxDaysOf(jj[0]) === 24,
       jj[0] && (jj[0].total + ' / ' + rxDaysOf(jj[0])));

    // (e) 漏字段：只有「每盒数量」，没写「单位剂量」—— 不许因此整味读错
    var half = rxParseKV('药品名称=样例辛片 | 每盒数量=30片 | 盒数=1 | 每次用量=1片 | 频次=每日一次');
    ck('漏了「单位剂量」也照样算：每盒 30 片、够 30 天',
       !!half && half.total === 30 && rxDaysOf(half) === 30, half && (half.total + ' / ' + rxDaysOf(half)));

    // (f) 一行 JSON（AI 常这么回，且引号就在键和冒号中间）
    var one = rxParseKV('{"药品名称":"样例壬片","单位剂量":"0.25g","每盒数量":"12粒",'
                      + '"盒数":"2","每次用量":"0.5g","频次":"每日2次","用法":"口服"}');
    ck('一行 JSON 也认：2 盒 × 12 粒、每次 2 粒',
       !!one && one.total === 24 && one.perDose === 2, one && (one.total + ' / ' + one.perDose));

    // (g) 人话里也有冒号 —— 只有一个字段的行不许当药单
    ck('「备注：饭后半小时」这种单冒号的句子不会被当成字段化药单',
       rxParseKV('备注：饭后半小时') === null);

    // (h) AI 在「用法」后面顺手带的时段词：只擦掉，不许写进备注
    var kvSlot = rxParseKV('药品名称=样例癸片 | 单位剂量=10mg | 每盒数量=20片 | 盒数=1 | '
                         + '每次用量=10mg | 频次=每日一次 | 用法=口服 早饭后');
    ck('字段化行尾带了「早饭后」→ 备注还是空的（不是「口服 早饭后」）',
       !!kvSlot && kvSlot.note === '', kvSlot && ('「' + kvSlot.note + '」'));
    ck('带了时段词也不影响数和频次',
       !!kvSlot && kvSlot.total === 20 && kvSlot.timesPerCycle === 1,
       kvSlot && (kvSlot.total + ' / ' + kvSlot.timesPerCycle));


    /* ============ ⑥b 就诊日期 / 开方日期（2026-11-20 加，11-21 扩成整张药方的起始时间） ============
       原话：「这个医生开的处方单里面是有就诊日期的，提示词要引导 AI 识别出来这个就诊日期，
              在回答里边把这个日期返回来，用来确定这张药方的起始时间。」
       真实处方（航空总院那张）印的是「就诊日期：2026-11-19」，不是「开方日期」——
       提示词原来只提「开方/开具日期」，协议里甚至写「不是就诊日期」，AI 因此不返回。
       ★ 现在这个日期同时管：医保锁 miStart + 余量基准 stockDate（已过的天自动扣药）。 */
    ck('提示词要求写开方日期（给了 YYYY-MM-DD 的样子）',
       RX_PROMPT.indexOf('开方日期') >= 0 && RX_PROMPT.indexOf('YYYY-MM-DD') >= 0);
    ck('★ 提示词引导 AI 认「就诊日期」——真实处方上印的就是这个（2026-11-21）',
       RX_PROMPT.indexOf('就诊日期') >= 0 && RX_PROMPT.indexOf('「就诊日期」') >= 0,
       RX_PROMPT.split('\n').filter(function(x){ return x.indexOf('就诊日期') >= 0; }).join(' / '));
    ck('提示词说清了这个日期是处方上打印的那天（不是今天）',
       RX_PROMPT.indexOf('处方上打印的那一天') >= 0,
       RX_PROMPT.split('\n').filter(function(x){ return x.indexOf('开方日期：') >= 0; }).join(' / '));
    // 提示词只引导 AI「把日期返回来」；余量怎么扣是页面的事（提示词里不许提天数/余量）
    ck('★ 提示词说清这是「药方的起始时间」，AI 别漏这一行',
       RX_PROMPT.indexOf('起始时间') >= 0 && RX_PROMPT.indexOf('别漏') >= 0);

    var DATE_CASES = [
      ['开方日期=2026-11-19', '2026-11-19'],
      ['就诊日期：2026-11-19', '2026-11-19'],   // ★ 真实处方印的就是这个（2026-11-21）
      ['就诊日期=2026/11/19', '2026-11-19'],
      ['打印日期：2026年11月19日 14:23', '2026-11-19'],
      ['日期：2026/3/8', '2026-03-08'],
      ['处方日期: 2026年11月19日', '2026-11-19'],
      ['开方日期=2026.11.9', '2026-11-09'],
      ['开方日期=2026-11-19 10:23', '2026-11-19'],
      ['开方日期=2026-02-30', ''],          // 不存在的日子别收
      ['开方日期=看不清', ''],
      ['药品名称=样例甲片 | 单位剂量=10mg', '']   // 药行不是日期行
    ];
    DATE_CASES.forEach(function(c){
      ck('日期写法「' + c[0] + '」→「' + c[1] + '」', rxKVDate(c[0]) === c[1], rxKVDate(c[0]));
    });

    var typedDate = rxParseText('日期：2026-10-02\n'
      + '样例甲片(某厂)[10mg*24片] 1盒 10mg 每日一次 口服\n'
      + '样例乙胶囊[0.25g*12粒] 2盒 1粒 每日2次 口服');
    ck('★ 手打的文字里夹一行「日期：」→ 还是 2 味药，日期不当第 3 味',
       typedDate.length === 2, typedDate.length + ' 味');
    ck('手打那行日期也捡出来了', typedDate.rxDate === '2026-10-02', typedDate.rxDate);

    var jsonDate = rxParseText('```json\n{"开方日期":"2026-11-19","药品":['
      + '{"药品名称":"样例甲片","单位剂量":"10mg","每盒数量":"24片","盒数":1,'
      + '"每次用量":"10mg","频次":"每日一次","用法":"口服"}]}\n```');
    ck('整段 JSON（对象上带开方日期）也认：1 味 + 日期',
       jsonDate.length === 1 && jsonDate.rxDate === '2026-11-19',
       jsonDate.length + ' 味 / ' + jsonDate.rxDate);

    // ★ AI 照抄真实处方，可能回 {"就诊日期":"2026-11-19",...} —— 2026-11-21 补的键
    var jsonVisit = rxParseText('```json\n{"就诊日期":"2026-11-19","药品":['
      + '{"药品名称":"样例甲片","单位剂量":"10mg","每盒数量":"24片","盒数":1,'
      + '"每次用量":"10mg","频次":"每日一次","用法":"口服"}]}\n```');
    ck('★ 整段 JSON 带「就诊日期」也认（原来这张键表漏了它 → 日期丢掉）',
       jsonVisit.length === 1 && jsonVisit.rxDate === '2026-11-19',
       jsonVisit.length + ' 味 / ' + jsonVisit.rxDate);

    var visitLine = rxParseText('就诊日期：2026-11-19\n'
      + '样例甲片(某厂)[10mg*24片] 1盒 10mg 每日一次 口服');
    ck('★ 手打/AI 回答里单独一行「就诊日期：」也捡出来，不当药',
       visitLine.length === 1 && visitLine.rxDate === '2026-11-19',
       visitLine.length + ' 味 / ' + visitLine.rxDate);

    /* ============ ⑦ 入库不碰药盒 ============ */

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

    /* ============ ⑧ 开方日期 → 医保锁从哪天起算（真实点按钮这条路） ============ */
    fixtureBox(); rxReset();
    q('#rxPaste').value = '[药单开始]\n开方日期=2026-01-05\n'
      + '药品名称=样例子片 | 单位剂量=10mg | 每盒数量=30片 | 盒数=1 | '
      + '每次用量=10mg | 频次=每日一次 | 用法=口服\n[药单结束]';
    rxTakeIn();          // 2026-11-20 起没有按钮了：粘进来就自动认
    ck('粘进黑框 → 日期格自动填成处方上的 2026-01-05',
       q('#rxDate').value === '2026-01-05', q('#rxDate').value);
    ck('★ 医保条上的解锁日按开方日算（不是按今天）',
       q('#rxInsTip').textContent.indexOf(fmtMD(miUnlockFrom('2026-01-05'))) >= 0,
       q('#rxInsTip').textContent);
    ck('日期不是今天时，格下小字改说「按 X 开的那张单子起算」',
       /不是今天/.test(q('#rxDateTip').textContent), q('#rxDateTip').textContent);

    await settle(submitRx({ preventDefault: function(){} }), ['知道了']);
    var sub1 = meds.filter(function(m){ return m.name === '样例子片'; })[0];
    ck('★ 入库的 miStart = 开方日期，不是 todayStr()',
       !!sub1 && sub1.miStart === '2026-01-05' && sub1.miStart !== todayStr(),
       sub1 && sub1.miStart);
    ck('★★ 入库的 stockDate 也 = 就诊日期，不是今天（2026-11-21：余量从开药那天起算）',
       !!sub1 && sub1.stockDate === '2026-01-05' && sub1.stockDate !== todayStr(),
       sub1 && sub1.stockDate);
    ck('★★ 一张 1 月开的单子：余量已按「过去的天数」扣掉（不会多算到今天）',
       !!sub1 && dosesUsed(sub1) > 0 && stockNow(sub1) < sub1.stockBase,
       sub1 && ('用掉 ' + dosesUsed(sub1) + ' 次 / 余 ' + stockNow(sub1)));
    ck('★ 年初开的单子按「开方日 + 23 天」早该解锁 —— 不会白锁到今天',
       !!sub1 && !miLocked(sub1), sub1 && ('锁着=' + miLocked(sub1)));

    // 自费那批：日期对它没意义，miStart 留空
    fixtureBox(); rxReset();
    q('#rxPaste').value = '开方日期=2026-01-05\n'
      + '药品名称=样例丑片 | 单位剂量=10mg | 每盒数量=30片 | 盒数=1 | '
      + '每次用量=10mg | 频次=每日一次 | 用法=口服';
    rxTakeIn();
    rxRows.forEach(function(r){ r.ins = ''; });
    await settle(submitRx({ preventDefault: function(){} }), ['知道了']);
    var sub2 = meds.filter(function(m){ return m.name === '样例丑片'; })[0];
    ck('自费那批不记 miStart（日期对它没意义）',
       !!sub2 && sub2.miStart === '', sub2 && ('「' + sub2.miStart + '」'));

    rxSetDate('2026-13-40');
    ck('日期格填了不存在的日子 → 退回今天（不会带着 13 月去算锁）',
       rxDateStart() === todayStr(), rxDateStart());

    /* ============ ⑨ 弹窗排成「1 2 3」三步，粘进来就认（2026-11-20 用户要求） ============
       原话：「信息太杂……第一步复制提示词、下边摆着提示词；第二步打开 AI（DeepSeek Kimi 豆包）、
       拍照片复制提示词；第三步把 AI 的回答粘到下边的框里……步骤的格式要统一，
       现在每一块还有不同的颜色，看起来很混乱。」
       另外两条：「框里不用填字」「粘到框里就直接解析成药品，不用再手点一下……旁边那两个按钮都去掉」。 */
    fixtureBox(); openRx(); await null;

    var steps = document.querySelectorAll('#dlgRx .rx-step');
    // 布局类的断言只能在这儿量：上面测剪贴板那段弹窗还没打开，元素高度都是 0。
    var _dr = q('#dlgRx');
    if(_dr && !_dr.open){ try{ _dr.showModal(); }catch(e){} }
    await new Promise(function(r){ setTimeout(r, 40); });
    (function(){
      var b = q('#btnRxTpl'), t = q('#rxStep1 .rx-step-t');
      if(!b || !t) return;
      var bc = b.getBoundingClientRect(), tc = t.getBoundingClientRect();
      ck('★ 复制按钮和标题在同一行（不再被挤到下一行）',
         bc.height > 0 && Math.abs((bc.top + bc.height / 2) - (tc.top + tc.height / 2)) < 8,
         '按钮中线 ' + (bc.top + bc.height / 2).toFixed(0) +
         ' / 标题中线 ' + (tc.top + tc.height / 2).toFixed(0));
      ck('按钮排在标题右边（不是压在标题上面）',
         bc.left > tc.left + tc.width - 4, '按钮 left ' + bc.left.toFixed(0));
    })();
    ck('弹窗里正好三步，用的是同一个 .rx-step 骨架（不再一块蓝一块绿）',
       steps.length === 3, steps.length + ' 步');
    var nos = [];
    for(var si = 0; si < steps.length; si++){
      var n = steps[si].querySelector('.rx-no');
      nos.push(n ? n.textContent.trim() : '?');
    }
    ck('三步的号码是 1 / 2 / 3，顺序没乱', nos.join('') === '123', nos.join('/'));
    // ★ 2026-11-20 用户把三步统一成「只留标题」：第 2、3 步的解释段也删了。
    ck('三步都只有「号码 + 标题」，下面不再挂解释段',
       !!steps[0].querySelector('.rx-step-t') && !!steps[1].querySelector('.rx-step-t')
       && !!steps[2].querySelector('.rx-step-t')
       && !steps[0].querySelector('.rx-step-d') && !steps[1].querySelector('.rx-step-d')
       && !steps[2].querySelector('.rx-step-d'));
    // 删掉的不只是不许再出现的那两段文字 —— 连样式也一起清理了
    ck('★ 解释段的样式 .rx-step-d 也一并删干净（不留死代码）',
       document.styleSheets.length > 0 &&
       (function(){
         var hit = false;
         for(var i = 0; i < document.styleSheets.length; i++){
           var rs; try{ rs = document.styleSheets[i].cssRules; }catch(e){ continue; }
           if(!rs) continue;
           for(var j = 0; j < rs.length; j++){
             if(rs[j].selectorText && rs[j].selectorText.indexOf('.rx-step-d') >= 0) hit = true;
           }
         }
         return !hit;
       })());
    ck('步骤标题依次是：复制提示词 / 打开一个 AI / 把回答粘到框里',
       /复制这段提示词/.test(steps[0].textContent)
       && /打开一个 AI/.test(steps[1].textContent)
       && /粘到下面这个框里/.test(steps[2].textContent),
       [steps[0].querySelector('.rx-step-t').textContent.slice(0, 12),
        steps[1].querySelector('.rx-step-t').textContent.slice(0, 14),
        steps[2].querySelector('.rx-step-t').textContent.slice(0, 14)].join(' / '));
    ck('第 2 步点名了 DeepSeek / Kimi / 豆包（用户原话里的三个例子）',
       /DeepSeek/.test(steps[1].textContent) && /Kimi/.test(steps[1].textContent)
       && /豆包/.test(steps[1].textContent));

    // ① 提示词原文就摆在第一步下面（不是只有个按钮）
    var tplTxt = q('#rxTplText') ? q('#rxTplText').textContent : '';
    ck('★ 第一步下面摆着提示词原文（和复制走的是同一份）',
       tplTxt.length > 300 && tplTxt === RX_PROMPT, tplTxt.length + ' 字');
    ck('提示词原文可选中（不是 aria-hidden 的装饰）',
       !q('#rxTplText').hidden && q('#rxTplText').offsetHeight > 0,
       q('#rxTplText').offsetHeight + 'px 高');

    // ② 粘贴框：白底（黑的是边框，不是整块），且有跑马灯边框
    var drop = q('#rxPaste').closest('.rx-drop');
    ck('粘贴框外面套着跑马灯那一层（.rx-drop）', !!drop);
    var cs = window.getComputedStyle(q('#rxPaste'));
    ck('★ 框里是白底黑字 —— 用户说的「黑框」只是边上那道框，里面要白的',
       /rgb\(255, 255, 255\)|rgba\(0, 0, 0, 0\)/.test(cs.backgroundColor), cs.backgroundColor);
    ck('框里没有预填的示例文字（不用填字，粘就行）',
       q('#rxPaste').value === '' && !q('#rxPaste').getAttribute('placeholder'),
       '「' + q('#rxPaste').value + '」/ placeholder=' + q('#rxPaste').getAttribute('placeholder'));
    var dropCs = drop ? window.getComputedStyle(drop, '::before') : null;
    ck('★ 边框那层真的在跑（::before 挂了旋转动画）',
       !!dropCs && /rxRun/.test(dropCs.animationName), dropCs && dropCs.animationName);
    ck('跑马灯是渐变底（conic-gradient），不是一道死板的纯色边',
       !!dropCs && /gradient/.test(dropCs.backgroundImage), dropCs && dropCs.backgroundImage.slice(0, 34));
    // ★ 2026-11-20 定稿：用户在页面里试完调色台，报的是
    //   「整卷彩带 松绿 墨玉 亮度 90」。调色台整段已经拆掉，现在守的是：
    //   ① 那套临时 UI 真的删干净了；② 渐变确实是松绿(h150)+墨玉(h170) 两段；
    //   ③ 整圈没有大片深色（他嫌「黑占比太多」）；④ 首尾同色，转起来不跳；
    //   ⑤ 峰值亮度落在「亮度 90」那一档，不是又暗回看不出光带。
    function _stops(){
      var b2 = window.getComputedStyle(drop, '::before').backgroundImage;
      return (b2.match(/rgba?\([^)]+\)/g) || []).map(function(x){
        return x.replace(/[^\d.,]/g, '').split(',').slice(0, 3).map(Number);
      });
    }
    function _lum(c){
      return (Math.max(c[0], c[1], c[2]) + Math.min(c[0], c[1], c[2])) / 510;
    }
    function _hue(c){
      var r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
      var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
      if(!d) return -1;
      var h;
      if(mx === r) h = ((g - b) / d) % 6;
      else if(mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      return h < 0 ? h + 360 : h;
    }
    ck('★ 调色台整套已拆干净（色板 / 铺法 / 滑块 / 那行配置文案都没了）',
       !q('#lampSw') && !q('#lampLv') && !q('#lampOut') && !q('#lampModeRow'),
       'sw=' + !!q('#lampSw') + ' lv=' + !!q('#lampLv') + ' out=' + !!q('#lampOut'));

    var _sts = _stops();
    var _hs = _sts.map(_hue).filter(function(h){ return h >= 0; });
    var _sg = _hs.filter(function(h){ return h >= 142 && h <= 160; }).length;
    var _my = _hs.filter(function(h){ return h >= 163 && h <= 180; }).length;
    ck('★ 定稿是「松绿 + 墨玉」两段绿带（色相 150° / 170°），不是一圈彩',
       _sg >= 2 && _my >= 2, _sts.length + ' 个色标，松绿 ' + _sg + ' / 墨玉 ' + _my +
       '（色相 ' + _hs.map(function(h){ return h.toFixed(0); }).join('/') + '）');

    function _minLum(){
      var m = 1;
      _sts.forEach(function(c){ if(_lum(c) < m) m = _lum(c); });
      return m;
    }
    function _peak(){
      var m = 0;
      _sts.forEach(function(c){ if(_lum(c) > m) m = _lum(c); });
      return m;
    }
    ck('★ 整圈都铺满颜色，最暗处也不发黑（用户嫌「黑占比太多」）',
       _minLum() > 0.35, '最暗色标亮度 ' + _minLum().toFixed(2) + '（0.47 那条接缝）');

    var _p0 = _sts[0], _pN = _sts[_sts.length - 1];
    ck('首尾同色，转一圈接缝处不跳色',
       _p0.join(',') === _pN.join(','), _p0.join(',') + ' vs ' + _pN.join(','));

    var _pk = _peak();
    ck('★ 峰值亮度是「亮度 90」那一档（0.58~0.67），看得见光带又不刺眼',
       _pk > 0.58 && _pk < 0.67, '峰值 ' + _pk.toFixed(2));

    ck('框底色也跟着松绿走（不再是那层蓝灰 #18202c）',
       /rgb\(1[0-9], 2[0-9], 2[0-9]\)|rgb\(14, 27, 20\)/.test(window.getComputedStyle(drop).backgroundColor),
       window.getComputedStyle(drop).backgroundColor);
    ck('跑马灯转得慢一些（≥5 秒一圈），不晃眼',
       parseFloat(dropCs && dropCs.animationDuration) >= 5, dropCs && dropCs.animationDuration);

    // ③ 三个旧按钮全没了：解析成药品 / 手动加一味 / 批量填用法
    ck('「解析成药品」按钮没了（粘进来就自动认）', !q('#btnRxParse'));
    ck('「手动加一味」按钮没了（要手加去主页加）', !q('#btnRxAddOne'));
    ck('「批量填用法 / 天数」按钮没了', !q('#btnRxUse'));
    ck('连那个弹窗本身也拆干净了（#dlgUse 不该还在 DOM 里）', !q('#dlgUse'));

    // ④ 手打/粘贴 → 自动认（不用点按钮）
    var box2 = q('#rxPaste');
    box2.value = '开方日期=2026-11-19\n'
      + '药品名称=样例卯片 | 单位剂量=10mg | 每盒数量=30片 | 盒数=1 | '
      + '每次用量=10mg | 频次=每日一次 | 用法=口服';
    box2.dispatchEvent(new Event('input', { bubbles: true }));
    ck('刚敲完还没到 0.7 秒时**不动**（免得把半截药名认成一行）',
       rxRows.length === 0, rxRows.length + ' 行');
    await new Promise(function(r){ setTimeout(r, 900); });
    ck('★ 停手 0.7 秒后自动认出 1 味（全程没点任何按钮）',
       rxRows.length === 1 && rxRows[0].name === '样例卯片',
       rxRows.map(function(x){ return x.name; }).join('/'));
    ck('认出来就清空框（免得手一抖粘两遍）', box2.value === '', '「' + box2.value + '」');
    ck('日期也跟着填上了', q('#rxDate').value === '2026-11-19', q('#rxDate').value);
    ck('下面给了句「认出几味」的回执', /认出/.test(q('#rxNote').textContent), q('#rxNote').textContent.slice(0, 22));

    // ⑤ 一个字的药都没认出来（比如 AI 回了个空药单）：原文留着 + 只提示，不清空、不弹框
    box2.value = '[药单开始]\n[药单结束]';
    box2.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(function(r){ setTimeout(r, 900); });
    ck('★ 一味都没认出来时原文留着（清空会让人以为粘丢了）', box2.value === '[药单开始]\n[药单结束]', box2.value);
    ck('认不出来会明说一句，不是默默无事发生', /没认出一味药/.test(q('#rxNote').textContent),
       q('#rxNote').textContent.slice(0, 24));

    // ⑥ 聊天里那句话不能被当成药名（按钮时代用户会看一眼，自动认没人看）
    box2.value = '抱歉，这张照片太糊了，我看不清。';
    box2.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(function(r){ setTimeout(r, 900); });
    ck('★ 「抱歉，这张照片太糊了」不会被认成一味药', rxRows.length === 1, rxRows.length + ' 行');
    ck('这种话也留原文（改一个字就能重认）', box2.value.indexOf('抱歉') === 0, box2.value.slice(0, 10));
    q('#rxPaste').value = '';

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
    dat = json.dumps({"ai": AI_REPLY, "typed": HAND_TYPED, "free": AI_FREE,
                      "nostar": NO_STAR, "kv": AI_KV, "kv2": AI_KV2, "json": AI_JSON},
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
