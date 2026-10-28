# -*- coding: utf-8 -*-
"""
drug-keeper：引导式填写（一次只问一件事）—— 真实 DOM 驱动测试。

用户 2026-10-28 的要求，原话：
    「你把所有要填写多个信息的 比如说添加药品里 要填怎么用 药名 规格 等等
      还有 又买了药里 要填又买了多少片 这笔是自费医保
      所有这些要填入多个信息的 都改成 一次只填一个信息 然后蹦出下一个
      下面有个进度栏 因为一看到这么多信息要填 用户本身就头大了 所以都做成引导式的」

这个改动的风险不在"多好看"，而在**别把数据路走岔**：
  ① 字段一个都不能少、name 一个都不能改名 —— 不然存档 / 备份导入 / 提交校验全乱；
  ② 校验不能因为"看不见"就失效 —— 备用药跳过的那一步必须真的不参与，
     daily 那几步必须真的拦得住；
  ③ 「下一步」不能顺手把整张表提交了（回车尤其容易）；
  ④ 走到最后一步点「保存」，存下来的东西要和原来逐字段一致。

做法同 test_box.py：把 index.html 复制成临时副本，在主 IIFE 内部末尾注入探针，
无头 Chrome --dump-dom 取回 #__T 里的 JSON。
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
  // 原生弹窗一律记成违规（2026-10-23 起页面里不该再出现）
  window.alert = function(m){ R.natives.push('alert: ' + String(m)); };
  window.confirm = function(m){ R.natives.push('confirm: ' + String(m)); return true; };
  window.prompt = function(m){ R.natives.push('prompt: ' + String(m)); return ''; };
  askAlert = function(o){
    R.alerts.push(typeof o === 'string' ? o : ((o.title || '') + ' ' + (o.sub || '')));
    return Promise.resolve();
  };
  askConfirm = function(o){ return Promise.resolve(true); };

  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function txt(el){ return el ? el.textContent.replace(/\s+/g, ' ').trim() : '(无)'; }

  // ---- 三个弹窗共同的取件口 ----
  function onStep(form){                     // 当前露出来的那一步
    var on = qa('#' + form + ' .wstep.on');
    return on.length === 1 ? on[0] : null;
  }
  function stepOf(form){
    var s = onStep(form);
    return s ? (s.getAttribute('data-wstep') || '') : '(没有/多步同时露)';
  }
  function nOn(form){ return qa('#' + form + ' .wstep.on').length; }
  function dots(form){ return qa('#' + form + ' .wizdots i'); }
  // 2026-10-28 用户要求进度栏只留图形、去掉「第 N 步 / 共 M 步」那行字，
  // 所以这里改成读图形本身：「一共几段 : 亮在第几段」。
  function nowAt(form){
    var d = dots(form);
    for(var i = 0; i < d.length; i++) if(d[i].className === 'now') return i + 1;
    return 0;
  }
  function bar(form){ return dots(form).length + ':' + nowAt(form); }
  function nextB(form){ return q('#' + form + ' [data-wiz=next]'); }
  function prevB(form){ return q('#' + form + ' [data-wiz=prev]'); }
  function errTxt(form){
    var e = onStep(form) ? onStep(form).querySelector('.wstep-err') : null;
    return (e && !e.hidden) ? txt(e) : '(没有红字)';
  }
  function tapNext(form){ nextB(form).click(); }
  function tapPrev(form){ prevB(form).click(); }
  function enter(form, el){                   // 输入框里敲回车
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true, cancelable: true }));
  }
  // 每一步都露在这一屏里吗（hidden 的父级 → offsetParent 为 null）
  function visible(el){ return !!el && el.offsetParent !== null; }
  function byName(n){
    for(var i = 0; i < meds.length; i++) if(meds[i].name === n) return meds[i];
    return null;
  }
  function todayStr2(){ return fmtDate(new Date()); }

  /* ================= 1. 添加药品：一打开只问一件事 ================= */
  var f = q('#formMed');
  openEditor(null);
  ck('打开「添加药品」→ 同时只露出一步', nOn('formMed') === 1, nOn('formMed'));
  ck('第一步问的是药名（不是一上来就让人填七八格）', stepOf('formMed') === 'name', stepOf('formMed'));
  ck('★ 进度栏只有图形，没有「第 N 步 / 共 M 步」那行字',
     txt(q('#formMed .wizbar')) === '' && !q('#formMed .wizline'),
     '"' + txt(q('#formMed .wizbar')) + '"' + (q('#formMed .wizline') ? ' / 还有 wizline' : ''));
  ck('图形是 8 段、亮在第 1 段', bar('formMed') === '8:1', bar('formMed'));
  ck('进度条分 8 段，第一段是当前段', dots('formMed').length === 8
     && dots('formMed')[0].className === 'now', dots('formMed').length + ' / ' + (dots('formMed')[0] || {}).className);
  ck('第一步不显示「上一步」（但位置留着，按钮不乱挪）',
     !prevB('formMed').classList.contains('can') && prevB('formMed').disabled === true,
     prevB('formMed').className);
  // 别的步骤是真的不在眼里，不是"挪到下面去了"
  ck('第二步那组单选此刻看不见', !visible(q('#kindRow')), String(visible(q('#kindRow'))));
  ck('「开药限制」那格此刻看不见', !visible(f.rxMax), String(visible(f.rxMax)));
  ck('★ 第一屏只露药名那一格（规格在自己那一步）',
     visible(f.name) && !visible(f.spec), visible(f.name) + ' / ' + visible(f.spec));

  /* ================= 2. 这一格没填就不让过 ================= */
  var n0 = meds.length;
  tapNext('formMed');
  ck('名称空着点「下一步」→ 停在第一步', stepOf('formMed') === 'name', stepOf('formMed'));
  ck('★ 就地一行红字，不弹窗打断', errTxt('formMed') === '先填「药品名称」', errTxt('formMed'));
  ck('没填的那格自己标红', f.name.classList.contains('wbad'), f.name.className);
  ck('没提交、没存进去', meds.length === n0, meds.length - n0);
  ck('全程没弹原生弹窗', R.natives.length === 0, R.natives.join(' | ') || '(没有)');

  /* ================= 3. 填好就走 → 上一步 / 回车 / 点进度条 ================= */
  f.name.value = '引导片';
  tapNext('formMed');
  ck('填上药名 → 走到第二步「规格」', stepOf('formMed') === 'spec', stepOf('formMed'));
  ck('走到第二步，亮的那段跟着挪到第 2 段', bar('formMed') === '8:2', bar('formMed'));
  ck('红字跟着这一步清掉了', errTxt('formMed') === '(没有红字)', errTxt('formMed'));

  tapNext('formMed');
  ck('★ 规格空着也能过（选填）→ 走到「怎么用」', stepOf('formMed') === 'kind', stepOf('formMed'));
  f.spec.value = '100mg';
  tapPrev('formMed');
  ck('「上一步」回到规格那步，刚填的字还在', stepOf('formMed') === 'spec' && f.spec.value === '100mg',
     stepOf('formMed') + ' / ' + f.spec.value);
  tapNext('formMed');
  ck('再「下一步」又回到「怎么用」（来回走不丢东西）', stepOf('formMed') === 'kind', stepOf('formMed'));

  // 进度条每一段点得动：直接跳到「怎么吃」
  dots('formMed')[3].click();
  ck('点进度条第 4 段 → 跳到「怎么吃」', stepOf('formMed') === 'dose', stepOf('formMed'));

  /* ================= 4. 用量那一步也要拦；回车 = 下一步，不是提交 ================= */
  f.perDose.value = '0';
  tapNext('formMed');
  ck('★ 每次用量填 0 → 拦住（0 等于没填用法）',
     stepOf('formMed') === 'dose' && /每次用量要大于 0/.test(errTxt('formMed')), errTxt('formMed'));
  f.perDose.value = '1';
  f.unit.value = '片';
  f.cycleDays.value = '1';
  f.timesPerCycle.value = '2';
  var before = meds.length;
  enter('formMed', f.perDose);
  ck('★ 在输入框里敲回车 = 下一步，不会把药直接存了',
     stepOf('formMed') === 'stock' && meds.length === before, stepOf('formMed') + ' / ' + (meds.length - before));
  ck('回车也没弹原生弹窗', R.natives.length === 0, R.natives.join(' | ') || '(没有)');

  /* ================= 5. 一路走到最后一步，存下来的东西要对 ================= */
  f.stockBase.value = '20';
  tapNext('formMed');
  ck('走到「开药限制」', stepOf('formMed') === 'limit', stepOf('formMed'));
  f.rxMax.value = '30';
  tapNext('formMed');
  ck('走到「这笔药怎么来的」', stepOf('formMed') === 'ins', stepOf('formMed'));
  f.ins.value = 'mi';
  syncInsUI();
  f.miStart.value = todayStr2();
  tapNext('formMed');
  ck('走到最后一步「备注」', stepOf('formMed') === 'note', stepOf('formMed'));
  ck('★ 到最后一步，「下一步」才变成「保存」', txt(nextB('formMed')) === '保存', txt(nextB('formMed')));
  ck('最后一步亮的是第 8 段（前面 7 段都算过去了）',
     bar('formMed') === '8:8' && dots('formMed').slice(0, 7).every(function(d){ return d.className === 'done'; }),
     bar('formMed'));

  var saved = null;
  nextB('formMed').click();                 // 点「保存」
  saved = byName('引导片');
  ck('★ 点「保存」真的存进去了', !!saved, saved ? '有' : '(没存)');
  ck('存完弹窗自己关了', !q('#dlgMed').open, String(q('#dlgMed').open));
  ck('存下来是「每天吃」+ 每天 2 片', !!saved && saved.kind === 'daily'
     && saved.perDose === 1 && saved.timesPerCycle === 2 && saved.cycleDays === 1,
     saved ? saved.kind + '/' + saved.perDose + '片×' + saved.timesPerCycle : '(没存)');
  ck('存下来余量 20、开药上限 30 天', !!saved && saved.stockBase === 20
     && saved.rxMax === 30 && saved.rxMaxDay === 1,
     saved ? saved.stockBase + ' / ' + saved.rxMax + ' / ' + saved.rxMaxDay : '(没存)');
  ck('存下来带医保标记和开药日', !!saved && saved.ins === 'mi' && !!saved.miStart,
     saved ? saved.ins + ' @ ' + saved.miStart : '(没存)');
  ck('规格也照填的存下来了', !!saved && saved.spec === '100mg', saved ? saved.spec : '(没存)');

  /* ================= 6. 备用药少一步：那一步整步不参与 ================= */
  openEditor(null);
  f.kind.value = 'prn';
  syncKindUI();
  ck('★ 选「备用药」→ 进度条从 8 段变 7 段', dots('formMed').length === 7, bar('formMed'));
  ck('★ 备用药没有「开药限制」这一步（不是灰着让人点）',
     WIZ.formMed.all.filter(function(s){ return s.getAttribute('data-wstep') === 'limit'; }).length === 1
     && wizList(WIZ.formMed).every(function(s){ return s.getAttribute('data-wstep') !== 'limit'; }),
     wizList(WIZ.formMed).map(function(s){ return s.getAttribute('data-wstep'); }).join(','));
  f.kind.value = 'daily';
  syncKindUI();
  ck('切回「每天吃」→ 段数也回到 8', dots('formMed').length === 8, bar('formMed'));
  q('#dlgMed').close();

  /* ================= 7. 每次打开都从第一步开始 ================= */
  openEditor(saved.id);                      // 编辑刚才存的那味（此刻停在最后一步过）
  ck('★ 再打开（编辑）也回到第 1 步，不是停在关掉时那步', stepOf('formMed') === 'name', stepOf('formMed'));
  ck('编辑时第 1 步就是原来填的药名', f.name.value === '引导片', f.name.value);
  ck('上次留下的红字没有跟进来', errTxt('formMed') === '(没有红字)', errTxt('formMed'));
  q('#dlgMed').close();

  /* ========== 7.5 「余量是哪天的」不再让人填（2026-10-28 用户要求） ========== */
  function agoD(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); }
  ck('★ 表单里没有「余量是哪天的」那一格了',
     qa('#formMed input[name=stockDate]').length === 0
       && qa('#formMed .wstep[data-wstep=stock] input').length === 1,
     qa('#formMed input[name=stockDate]').length + ' 格');
  ck('★ 整个弹窗里也不再出现「余量日期」「哪天的」这些字',
     txt(q('#formMed')).indexOf('余量日期') < 0 && txt(q('#formMed')).indexOf('哪天的') < 0,
     txt(q('#formMed')).slice(0, 40));
  ck('「还剩多少」那一步只剩一个输入框，进度条照样 8 段',
     dots('formMed').length === 8, bar('formMed'));

  // 造一味「10 天前数了 30 片、每天 1 片」的老药 —— 今天推算出来是 20 片
  var oldMed = { id: 'wizold', kind: 'daily', name: '旧基准片', spec: '', perDose: 1, unit: '片',
                 cycleDays: 1, timesPerCycle: 1, stockBase: 30, stockDate: agoD(10),
                 rxMax: 30, rxMaxDay: 1, note: '' };
  meds.push(oldMed);
  openEditor(oldMed.id);
  ck('编辑老药时，预填的仍然是「扣到今天」的数（不是 30）',
     num(f.stockBase.value) === 20, f.stockBase.value);
  f.spec.value = '3mg';                        // 只改别的项，一概不碰余量
  await submitMed({ preventDefault: function(){} });
  var a1 = byName('旧基准片');
  ck('★ 没动余量 → 原来的基准（30 片 / 10 天前）原样留着，不因为点了保存就滚到今天',
     a1.stockBase === 30 && a1.stockDate === agoD(10), a1.stockBase + ' / ' + a1.stockDate);
  ck('顺手改的规格确实存进去了', a1.spec === '3mg', a1.spec);

  openEditor(oldMed.id);
  f.stockBase.value = '15';                    // 今天真数了一遍：15 片
  await submitMed({ preventDefault: function(){} });
  var a2 = byName('旧基准片');
  ck('★ 改了余量 → 按「今天数出来的」存，基准日就是今天（不用再问是哪天）',
     a2.stockBase === 15 && a2.stockDate === todayStr2(), a2.stockBase + ' / ' + a2.stockDate);
  ck('改完这个基准，卡片上的天数也跟着变',
     Math.abs(daysLeft(a2) - 15) < 1e-9, daysLeft(a2));
  q('#dlgMed').close();

  /* ================= 8. 又买了药：先问数量，再问来路 ================= */
  openEditor(null);
  f.name.value = '补货引导片';
  f.kind.value = 'daily'; syncKindUI();
  f.perDose.value = 1; f.unit.value = '片'; f.cycleDays.value = 1; f.timesPerCycle.value = 1;
  f.stockBase.value = 4;
  f.rxMax.value = 30; f.ins.value = 'self'; f.note.value = '';
  await submitMed({ preventDefault: function(){} });
  var rest = byName('补货引导片');
  ck('备好一味只有 4 片的药', !!rest && rest.stockBase === 4, rest ? rest.stockBase : '(没存)');

  restockMed(rest.id);
  await null;
  ck('「又买了药」是两步：先问数量', stepOf('formNum') === 'val' && bar('formNum') === '2:1',
     stepOf('formNum') + ' / ' + bar('formNum'));
  ck('弹窗里只有数量那一格露着（自费/医保还没问）', visible(q('#numVal')) && !visible(q('#numIns')),
     visible(q('#numVal')) + ' / ' + visible(q('#numIns')));
  q('#numVal').value = '';
  tapNext('formNum');
  ck('数量空着点「下一步」→ 拦住并说明', stepOf('formNum') === 'val' && /先填/.test(errTxt('formNum')),
     errTxt('formNum'));
  q('#numVal').value = '0';
  tapNext('formNum');
  ck('数量填 0 → 也拦住（又买了 0 片没意义）', stepOf('formNum') === 'val', errTxt('formNum'));
  q('#numVal').value = '28';
  tapNext('formNum');
  ck('填 28 → 走到第二步「自费 / 医保」', stepOf('formNum') === 'ins', stepOf('formNum'));
  ck('这一步按钮变成「加到库存」', txt(nextB('formNum')) === '加到库存', txt(nextB('formNum')));
  ck('这一步「上一步」回来了', prevB('formNum').classList.contains('can'), prevB('formNum').className);
  q('#formNum input[name=numIns][value=mi]').checked = true;
  syncNumIns();
  tapNext('formNum');
  await null;
  ck('★ 两步走完，库存 4 + 28 = 32', (byName('补货引导片') || {}).stockBase === 32,
     (byName('补货引导片') || {}).stockBase);
  ck('顺带记成了医保开的', (byName('补货引导片') || {}).ins === 'mi', (byName('补货引导片') || {}).ins);

  /* ================= 9. 校准余量只有一格：不该出现进度条 ================= */
  var cal = byName('补货引导片');
  calibMed(cal.id);
  await null;
  ck('「校准余量」只有一步 → 进度条不出现（一格的表不配进度条）',
     q('#formNum .wizdots').hidden === true
       && txt(q('#formNum .wizbar')) === '', String(q('#formNum .wizdots').hidden));
  ck('只有一步时按钮直接就是那件事（校准余量 → 「校准」），不写「下一步」',
     txt(nextB('formNum')) === '校准', txt(nextB('formNum')));
  q('#numVal').value = '9';
  tapNext('formNum');
  await null;
  ck('校准到 9 也照常生效', (byName('补货引导片') || {}).stockBase !== undefined
     && Math.abs(stockNow(byName('补货引导片')) - 9) < 1e-9, stockNow(byName('补货引导片')));

  /* ================= 10. 设置也是引导式 ================= */
  openSettings();
  ck('设置打开：5 段、亮在第 1 段', bar('formSet') === '5:1', bar('formSet'));
  var names = [];
  for(var k = 0; k < 5; k++){
    names.push((onStep('formSet') || { getAttribute: function(){ return '?'; } }).getAttribute('data-name'));
    if(k < 4) tapNext('formSet');
  }
  ck('五步依次是：偏少线 / 补货目标 / 医保间隔 / 提前解锁 / 家庭成员',
     names.join(' > ') === '偏少线 > 补货目标 > 医保间隔 > 提前解锁 > 家庭成员', names.join(' > '));
  ck('最后一步才轮到家庭成员那块', stepOf('formSet') === 'people', stepOf('formSet'));
  ck('此时的按钮是「保存」', txt(nextB('formSet')) === '保存', txt(nextB('formSet')));
  tapNext('formSet');
  ck('设置保存后弹窗关闭', !q('#dlgSet').open, String(q('#dlgSet').open));

  /* ================= 11. 字段一个没少、name 一个没改 ================= */
  var wantInputs = ['name', 'spec', 'perDose', 'unit', 'cycleDays', 'timesPerCycle',
                    'stockBase', 'rxMax', 'miStart', 'note'];
  var missIn = wantInputs.filter(function(n){
    return qa('#formMed input[name="' + n + '"]').length !== 1;
  });
  ck('★ 添加药品那 10 个输入框一个没少、也没重复（引导式没造第二套字段）',
     missIn.length === 0, missIn.join(',') || '10/10');
  ck('单选组 kind / ins 也各只有一组',
     qa('#formMed input[name=kind]').length === 2 && qa('#formMed input[name=ins]').length === 2,
     qa('#formMed input[name=kind]').length + ' / ' + qa('#formMed input[name=ins]').length);
  ck('三张表都带 novalidate（校验交给页面自己，不弹浏览器气泡）',
     q('#formMed').hasAttribute('novalidate') && q('#formSet').hasAttribute('novalidate')
     && q('#formNum').hasAttribute('novalidate'), '');
  ck('三张表都还是原来那个 <form>（id 没变，提交链没动）',
     !!q('#formMed') && !!q('#formSet') && !!q('#formNum'), '');
  ck('全程一次原生弹窗都没有', R.natives.length === 0, R.natives.join(' | ') || '(没有)');

  /* ================= 12. 静态口径：别把 .wstep 改成 wrapper div ================= */
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

    # ---- 源码级：`.wstep` 必须还是 display:contents（改成 wrapper div 栅格就散）----
    src_cases = []
    src_cases.append({
        "name": "源码里 .wstep 的显示方式是 display:contents（不动原栅格）",
        "pass": ".wstep.on{display:contents}" in src,
        "got": "有" if ".wstep.on{display:contents}" in src else "没有",
    })
    src_cases.append({
        "name": "每张引导式表单都有一个 .wizbar 进度栏",
        "pass": src.count('<div class="wizbar"></div>') == 3,
        "got": src.count('<div class="wizbar"></div>'),
    })

    tmpdir = tempfile.mkdtemp(prefix="dk_wiz_")
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
    data["cases"] = data["cases"] + src_cases    # 源码级那两条不进页面，直接并进来

    fails = [c for c in data["cases"] if not c["pass"]]
    for c in data["cases"]:
        print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c.get("got")))
    print("\n" + "-" * 60)
    print("通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
