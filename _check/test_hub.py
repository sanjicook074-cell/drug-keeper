# -*- coding: utf-8 -*-
"""
drug-keeper：网页端「局域网同步」端到端自检。

用户 2026-11-20 的要求（原话大意）：
    「桌面版和手机 APP、网页版互联，桌面版起同步数据的作用，三者在一个局域网里。」

这里不打桩。真起一个 node 服务（desktop/server.js，换个端口），用真的无头 Chrome 打开
真的 index.html（file:// 加载，跟用户双击打开一模一样），然后：
   ① 点「上传到电脑」→ 电脑那边的主档得收到，且**页面自己**得说一句「已存到电脑」；
   ② 先往电脑里塞一份别处的数据 → 点「从电脑拉取」→ 确认框点「全部替换」→ 本机数据得换掉；
   ③ file:// 到 http:// 是跨域，这条能通全靠服务端 CORS 全开 —— 所以顺带盯着它。

★ 探针必须注入在主 IIFE 内部：hubCall / people / store 都在里面，外面看不见。
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = drug-keeper/
SRC = os.path.join(ROOT, "index.html")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 18732
BASE = "http://127.0.0.1:%d" % PORT

PROBE = r"""
/* ===== 注入的测试代码（必须在主 IIFE 内部）===== */
(async function(){
  var R = { cases: [] };
  function ck(n, c, g){ R.cases.push({ name: n, pass: !!c, got: g }); }
  function q(s){ return document.querySelector(s); }
  var BASE = '__BASE__';

  try {
    /* ① UI 在不在 */
    ck('「导入导出」里有局域网同步那一节（地址 + 两个动作）',
       !!q('#hubAddr') && !!q('#btnHubUp') && !!q('#btnHubDown') &&
       !!q('#hubNote'), '');
    // 2026-11-24：左上角版本号徽标 + 双端取值（手机 appVersion / 桌面 apk.ver）
    ck('★ 左上角「药」标旁有版本号徽标（手机读 APK、桌面读服务端）',
       !!q('#appVer') && q('#appVer').parentElement.tagName === 'H1' &&
       /AA\.appVersion/.test(document.documentElement.innerHTML) &&
       /st\.apk && st\.apk\.ver/.test(document.documentElement.innerHTML), '');
    ck('两个动作是「上传到电脑」「从电脑拉取」',
       /上传到电脑/.test((q('#btnHubUp') || {}).textContent || '') &&
       /从电脑拉取/.test((q('#btnHubDown') || {}).textContent || ''), '');

    /* ①b 2026-11-20 用户要求：加号二选一 + 连接入口 + 时钟校准 */
    ck('★ 加号菜单存在：底方录入(primary/蓝) + 添加药品(ghost/白)',
       !!q('#dlgAdd') && !!q('#btnAddViaRx') && !!q('#btnAddViaOne') &&
       /primary/.test(q('#btnAddViaRx').className) &&
       /ghost/.test(q('#btnAddViaOne').className) &&
       /底方录入/.test(q('#btnAddViaRx').textContent) &&
       /添加药品/.test(q('#btnAddViaOne').textContent),
       (q('#btnAddViaRx') || {}).className + ' / ' + (q('#btnAddViaOne') || {}).className);
    ck('★ 桌面顶栏有「＋ 添加」和「连接手机」入口',
       !!q('#btnAddMenu') && /连接手机/.test((q('#btnConnect') || {}).textContent || ''), '');
    ck('★ 连接手机按钮就在「设置」左边、是顶栏第一个（2026-11-20 晚用户要求）',
       (function(){
         var bar = q('.top-actions');
         if(!bar) return false;
         var kids = Array.prototype.slice.call(bar.children).filter(function(b){
           return getComputedStyle(b).display !== 'none' && b.offsetParent !== null;
         });
         return kids.length >= 2 && kids[0].id === 'btnConnect' && kids[1].id === 'btnSettings';
       })(), '');
    ck('★ 手机连接弹窗有精简模式样式（只留配对码框，结果用 toast 弹）',
       /#dlgConnect\.slim-connect/.test(document.querySelector('style') ? document.documentElement.innerHTML : '') ||
       /slim-connect/.test(document.documentElement.innerHTML), '');
    ck('★ 手机三个点旁有「连接电脑」入口',
       !!q('#btnConnectPhone') && !!q('#dlgConnect'), '');
    ck('★ 同步区已搬进独立的「连接」窗（不再是藏在导入导出里）',
       !!(q('#connectBody') && q('#connectBody').querySelector('#hubCode')), '');
    ck('底方录入在「更多」列表里也标蓝',
       !!(q('.moreitem.act-rx') && /底方录入/.test(q('.moreitem.act-rx').textContent)), '');
    // 时钟校准：喂一个和本机差 2 小时的服务端时间，hubClockNow 应该跟过去
    var realOff = Number(store.get('dk.hub.clockoff') || 0);
    hubCalibrate(Date.now() + 7200000);
    var calibrated = Math.abs(hubClockNow() - (Date.now() + 7200000)) < 3000;
    store.set('dk.hub.clockoff', String(realOff));   // 还原，别影响下面的断言
    ck('★ 时钟校准：两端差 2 小时也能换算到同一坐标系（修同步不上的根因）', calibrated, hubClockNow() - Date.now());

    /* ①c 手机连接弹窗精简：挂上 slim-connect 后，地址/上传/说明真的被 CSS 藏掉。
       自检环境没有手机壳，bindConnect 不会自动挂类，这里手动挂一下验证规则本身。 */
    (function(){
      var d = q('#dlgConnect');
      if(!d || !q('#hubAddr')) return;
      d.classList.add('slim-connect');
      var addrDisp = getComputedStyle(q('#hubAddr')).display;
      var up = q('#btnHubUp');
      var upDisp = up ? getComputedStyle(up).display : '(无)';
      // 上传/拉取的 morelist 包裹（含 btnHubDown）也是 connectBody 的直接子元素
      var down = q('#btnHubDown'), wrap = down && down.parentElement;
      var wrapDisp = wrap && wrap !== q('#connectBody') ? getComputedStyle(wrap).display : '(无)';
      var noteDisp = getComputedStyle(q('#hubNote')).display;
      d.classList.remove('slim-connect');
      ck('★ 手机精简模式：挂 slim-connect 后地址/上传/说明全被 CSS 藏起来',
         addrDisp === 'none' && upDisp === 'none' && wrapDisp === 'none' && noteDisp === 'none',
         addrDisp + '/' + upDisp + '/' + wrapDisp + '/' + noteDisp);
    })();
    ck('★ 手机壳加载时会挂 slim-connect、结果走 toast（源码级）',
       /window\.AndroidApp && window\.AndroidApp\.findHub && \$\('#dlgConnect'\)/.test(document.documentElement.innerHTML) &&
       /classList\.add\('slim-connect'\)/.test(document.documentElement.innerHTML) &&
       /function hubPairToast/.test(document.documentElement.innerHTML), '');

    /* ①d 2026-11-24：配对状态持久化 + 自动重发现（修「更新后断联」）。
       自检环境无手机壳，这里塞一个假的 AndroidApp 来验证 save/restore 逻辑本身。 */
    ck('★ 源码含配对码常量 + 快照函数（连一次就一直连着）',
       /const LS_HUBCODE = 'dk\.hub\.code'/.test(document.documentElement.innerHTML) &&
       /function hubSyncSnapshot/.test(document.documentElement.innerHTML) &&
       /function hubRestoreSync/.test(document.documentElement.innerHTML) &&
       /function hubRediscover/.test(document.documentElement.innerHTML), '');
    (function(){
      // 备份一份，测完还原，别污染后面的用例
      var savedLS = {};
      ['dk.hub','dk.hub.code','dk.hub.dev','dk.hub.local','dk.hub.seen','dk.hub.clockoff'].forEach(function(k){
        savedLS[k] = store.get(k);
      });
      var fakeStore = {};
      var fakeAA = {
        findHub: function(){ return ''; },
        saveSync: function(json){ fakeStore.state = json; },
        readSync: function(){ return fakeStore.state || ''; }
      };
      var hadAA = window.AndroidApp;
      window.AndroidApp = fakeAA;
      try {
        // 配对成功 → 地址 + 配对码 + 时间戳都进快照
        store.set('dk.hub', 'http://192.168.1.9:8730');
        store.set('dk.hub.code', '123456');
        store.set('dk.hub.seen', '1700000000000');
        hubSaveSync();
        var snap = JSON.parse(fakeStore.state || '{}');
        ck('★ saveSync 把地址/配对码/时间戳都存进原生',
           snap.hub === 'http://192.168.1.9:8730' && snap.code === '123456' &&
           snap.seen === '1700000000000', JSON.stringify(snap));
        // 模拟换页后 localStorage 丢了地址 → readSync 回填
        store.set('dk.hub', '');
        store.set('dk.hub.code', '');
        hubRestoreSync();
        ck('★ 换页后地址/配对码从原生自动回填（不用重新输验证码）',
           (store.get('dk.hub') || '') === 'http://192.168.1.9:8730' &&
           (store.get('dk.hub.code') || '') === '123456',
           (store.get('dk.hub') || '') + ' / ' + (store.get('dk.hub.code') || ''));
        // 地址失效 → findHub 用记住的配对码重新发现
        HUB_LAST_DISCOVER = 0;
        store.set('dk.hub', 'http://10.0.0.1:8730');   // 旧地址
        fakeAA.findHub = function(code){
          return code === '123456' ? 'http://192.168.1.77:8730' : '';
        };
        var fresh = hubRediscover();
        ck('★ 旧地址失效时用配对码自动发现新地址', fresh === 'http://192.168.1.77:8730' &&
           (store.get('dk.hub') || '') === 'http://192.168.1.77:8730', fresh);
      } finally {
        window.AndroidApp = hadAA;
        Object.keys(savedLS).forEach(function(k){
          if(savedLS[k] == null) store.remove ? store.remove(k) : store.set(k, '');
          else store.set(k, savedLS[k]);
        });
      }
    })();

    /* ①e 2026-11-24（4.5）：手机 App 的「验证版本是否同步」按钮。
       用真服务端 + 假 AndroidApp（给 pageMd5 / appVersion）走完整链路。 */
    ck('★ 源码有 hubVerifyVersion + 主动换页入口（手机验证按钮的后端）',
       /function hubVerifyVersion/.test(document.documentElement.innerHTML) &&
       /window\.__dkPullPage\s*=\s*pullPageUpdate/.test(document.documentElement.innerHTML) &&
       /pullPageUpdate\(force\)/.test(document.documentElement.innerHTML), '');
    ck('★ 连接弹窗里有「验证版本是否同步」按钮（平时藏、手机壳里露）',
       !!q('#btnVerCheck') && /验证版本是否同步/.test((q('#btnVerCheck') || {}).textContent || '') &&
       !!q('#verCheckNote') && !!q('#hubVerRow'), '');
    (function(){
      var d = q('#dlgConnect'), row = q('#hubVerRow');
      if(!d || !row) return;
      var before = getComputedStyle(row).display;
      d.classList.add('slim-connect');
      var after = getComputedStyle(row).display;
      d.classList.remove('slim-connect');
      ck('★ 验证按钮只在真机壳精简弹窗里出现（桌面/网页不打扰）',
         before === 'none' && after !== 'none', before + ' → ' + after);
    })();
    await (async function(){
      var savedHub = store.get('dk.hub'), hadAA = window.AndroidApp;
      try {
        // 没连电脑 → 说人话，不 fetch
        store.set('dk.hub', '');
        var off = await hubVerifyVersion();
        ck('★ 没连电脑时验证按钮给出「先输配对码」提示',
           off.ok === false && off.why === 'offline' &&
           /配对码/.test((q('#verCheckNote') || {}).textContent || ''),
           JSON.stringify(off));
        // 连上且一致 → 页面 md5 / APK 版本都对上
        store.set('dk.hub', BASE);
        var md5 = '', apkVer = '';
        try {
          var rm = await fetch(BASE + '/api/page/meta', { cache:'no-store' });
          md5 = String((await rm.json()).md5 || '');
          var rs = await fetch(BASE + '/api/state', { cache:'no-store' });
          apkVer = String(((await rs.json()).apk || {}).ver || '');
        } catch(e){}
        window.AndroidApp = {
          findHub: function(){ return BASE; },
          pageMd5: function(){ return md5; },
          appVersion: function(){ return apkVer; }
        };
        var same = await hubVerifyVersion();
        ck('★ 真服务端 + 假壳：页面 md5 与 APK 版本都一致时报「已同步」',
           same.ok === true && same.pageOk === true && same.apkOk === true &&
           /已同步/.test((q('#verCheckNote') || {}).textContent || '') &&
           /版本已同步/.test((q('#toast') || {}).textContent || ''),
           JSON.stringify(same));
        // md5 不一致 → 明确报不一致，不弹系统框（__dkPullPage 在无壳自检里不存在）
        window.AndroidApp.pageMd5 = function(){ return '00000000000000000000000000000000'; };
        var diff = await hubVerifyVersion();
        ck('★ 页面版本不一致时明确报出来（提示正在更新，不静悄悄）',
           diff.pageOk === false && /不一致/.test((q('#verCheckNote') || {}).textContent || '') &&
           /正在更新/.test((q('#toast') || {}).textContent || ''),
           JSON.stringify(diff));
      } finally {
        window.AndroidApp = hadAA;
        if(savedHub == null) store.remove ? store.remove('dk.hub') : store.set('dk.hub', '');
        else store.set('dk.hub', savedHub);
      }
    })();

    /* ② 地址规范化：用户多半只会抄 IP:端口，不带 http:// */
    store.set('dk.hub', '127.0.0.1:__PORT__');
    ck('★ 地址只写「IP:端口」也能用（自动补 http://）',
       hubBase() === BASE, hubBase());
    store.set('dk.hub', BASE + '/');
    ck('地址末尾多一个斜杠也不会拼出 //api', hubBase() === BASE, hubBase());
    store.set('dk.hub', '127.0.0.1:__PORT__');

    /* ③ 上传：页面 → 电脑
       ★ 先造一味药。空库上传会被服务端拒掉 —— 那是对的（别拿空数据覆盖主档），
         这里要测的是"有数据时能不能真传上去"。 */
    people[0].meds.push({
      id: 't1', name: '自测药', spec: '1mg', perDose: 1, unit: '片', cycleDays: 1,
      timesPerCycle: 1, stockBase: 5, stockDate: '2026-11-01', rxMax: 5, rxMaxDay: 1, note: ''
    });
    save();
    var before = { people: people.length, meds: people.reduce(function(n, p){ return n + p.meds.length; }, 0) };
    await hubCall('up');
    var note1 = (q('#hubNote') || {}).textContent || '';
    ck('★ 点「上传到电脑」后页面明确说传上去了',
       /已存到电脑/.test(note1), note1.slice(0, 46));

    /* ④ 往电脑里塞一份「别处来的」数据，再把本机那份拉回来 —— 走真实接口 */
    var fake = JSON.stringify({
      people: [
        { id: 'c1', name: '电脑甲', meds: [
            { id: 'm1', name: '电脑上的药', spec: '10mg', perDose: 1, unit: '片',
              cycleDays: 1, timesPerCycle: 1, stockBase: 10, stockDate: '2026-11-01',
              rxMax: 10, rxMaxDay: 1, note: '' }
          ], box: ['m1', '', ''], stage: [] },
        { id: 'c2', name: '电脑乙', meds: [], box: ['', '', ''], stage: [] }
      ],
      currentId: 'c1'
    });
    var pr = await fetch(BASE + '/api/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        deviceId: 'another', deviceName: '另一台电脑', kind: 'web',
        clientTime: Date.now() + 5000, data: fake, settings: '{}',
        people: 2, meds: 1, force: true
      })
    });
    ck('另一台设备也能把数据推进这台电脑的主档', pr.ok, pr.status);

    /* 拉取会弹确认框（整包替换），自动点「全部替换」 */
    var guard = setInterval(function(){
      var d = q('#dlgAsk');
      if(d && d.open){
        var foot = q('#askFoot');
        var btns = foot ? foot.querySelectorAll('button') : [];
        for(var i = 0; i < btns.length; i++){
          if(/全部替换/.test(btns[i].textContent)){ btns[i].click(); break; }
        }
      }
    }, 25);
    await hubCall('down');
    await new Promise(function(r){ setTimeout(r, 120); });
    clearInterval(guard);

    ck('★ 点「从电脑拉取」+ 确认后，本机数据真的换成了电脑那份',
       people.length === 2, people.length + ' 个人');
    ck('换过来的就是电脑上那份（人名对得上，不是凭空生成）',
       people[0] && people[0].name === '电脑甲', (people[0] || {}).name);
    ck('药也跟着过来了', people[0] && people[0].meds.length === 1,
       people[0] && people[0].meds.length);
    var note2 = (q('#hubNote') || {}).textContent || '';
    ck('拉完会说清楚「这就是电脑上的主档」', /电脑上那份/.test(note2), note2.slice(0, 46));

    /* ⑤ 地址空着的时候要说人话，不能静默失败 */
    store.set('dk.hub', '');
    await hubCall('up');
    var note3 = (q('#hubNote') || {}).textContent || '';
    ck('没填地址 → 提示先填地址，而不是默默没反应',
       /先在上面填/.test(note3), note3.slice(0, 40));

    /* ⑥ 配对码 + 自动双向同步（2026-11-20 用户要求）
       先关掉页面自己的定时器和 debounce，免得测试期间它们抢互斥锁、干扰判定。 */
    store.set('dk.hub', '127.0.0.1:__PORT__');
    try { clearInterval(HUB_AUTO_TIMER); } catch(e){}
    try { clearTimeout(hubAutoT); } catch(e){}

    ck('配对码输入框存在（手机壳里会显示）', !!q('#hubCode') && !!q('#btnHubPair'), '');

    /* 配对码格式：不足 6 位要被挡下，不能真的去广播 */
    q('#hubCode').value = '123';
    store.set('dk.hub', '');
    await hubPair();
    ck('配对码不足 6 位 → 提示 6 位数字、不去连',
       /6 位数字/.test((q('#hubNote') || {}).textContent || '') &&
       !store.get('dk.hub'), ((q('#hubNote') || {}).textContent || '').slice(0, 30));
    store.set('dk.hub', '127.0.0.1:__PORT__');

    /* 注入假的 findHub：广播拿到地址 → 存下来 */
    window.AndroidApp = { findHub: function(c){ return c === '123456' ? '__BASE__' : ''; } };
    q('#hubCode').value = '123456';
    await hubPair();
    ck('★ 配对：输 6 位码广播拿到地址并存下来',
       store.get('dk.hub') === '__BASE__', store.get('dk.hub'));

    /* 自动同步决策：本机改动比 seen 新 → 自动推，且不弹确认框 */
    // 上一步配对码出错时 focus 留在输入框，自动同步会因此跳过 —— 先移走焦点
    if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
    var t0 = Date.now();
    store.set('dk.hub.seen', String(t0 - 5000));   // 上次对齐点在过去
    store.set('dk.hub.local', String(t0));          // 本机刚改过，比 seen 新
    var acted = await hubAutoSync();
    ck('★ 本机有改动 → 自动同步自动推上去（acted=up）', acted === 'up', acted);
    ck('自动同步推完，seen 被推平（不会反复推）',
       Number(store.get('dk.hub.seen')) >= t0 - 1000, store.get('dk.hub.seen'));
    ck('自动同步不弹确认框（静默）', !document.querySelector('dialog[open]'), '');

    /* 主档更新、本机没改 → 自动静默拉 */
    var seenNow = Number(store.get('dk.hub.seen'));
    var masterTime = seenNow + 8000;           // 主档比本机 seen 新
    var pushNew = JSON.stringify({
      people: [{ id: 'z1', name: '自动来的', meds: [], box: ['', '', ''], stage: [] }],
      currentId: 'z1'
    });
    await fetch(BASE + '/api/sync', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deviceId: 'remote', deviceName: '电脑', kind: 'desktop',
        clientTime: masterTime, data: pushNew, settings: '{}',
        people: 1, meds: 0, force: true })
    });
    // 本机时间戳压回 seen（不要被刚才的推干扰），让「主档 > seen」这一支生效
    store.set('dk.hub.local', String(seenNow));
    acted = await hubAutoSync();
    ck('★ 主档更新、本机没改 → 自动静默拉下来（acted=down）', acted === 'down', acted);
    ck('自动拉下来的数据真的进了本机', people.length === 1 && people[0].name === '自动来的',
       people.length + ':' + ((people[0] || {}).name || ''));
    ck('自动同步拉完，seen 也推平', Number(store.get('dk.hub.seen')) >= masterTime - 1000,
       store.get('dk.hub.seen'));

    /* 首启竞争：从没对齐过（seen=0）时以主档为准，不能被本机 local 误导推空数据上去 */
    store.set('dk.hub.seen', '0');
    store.set('dk.hub.local', String(Date.now() + 60000));   // 本机 local 故意造得很大
    acted = await hubAutoSync();
    ck('★ 首启 seen=0、主档有数据 → 以主档为准拉下来，不被本机时间戳带偏',
       acted === 'down', acted);
    ck('首启拉下来的还是主档那份', people.length === 1 && people[0].name === '自动来的',
       people.length + ':' + ((people[0] || {}).name || ''));

    /* 有弹窗开着时不自动动数据（免得盖掉正在填的东西） */
    var dlg = q('#dlgAsk');
    if(dlg && !dlg.open){ try { dlg.showModal(); } catch(e){} }
    store.set('dk.hub.local', String(Date.now() + 20000));
    acted = await hubAutoSync();
    ck('弹窗开着时自动同步先跳过（不动数据）', acted === null, acted);
    try { dlg.close(); } catch(e){}
  } catch(e){
    R.cases.push({ name: '探针自己抛异常了', pass: false,
                   got: String(e && e.message || e) });
  }

  var pre = document.createElement('pre');
  pre.id = '__T';
  pre.textContent = JSON.stringify(R);
  document.body.appendChild(pre);
})();
"""


def wait_ready(proc, secs=25):
    t0 = time.time()
    while time.time() - t0 < secs:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return None
            continue
        if line.startswith("READY"):
            try:
                return json.loads(line[5:].strip())
            except Exception:
                return {"raw": line.strip()}
    return None


def main():
    node = shutil.which("node")
    if not node:
        print("没找到 node，跳过")
        return 2

    tmp = tempfile.mkdtemp(prefix="dk_hub_")
    env = os.environ.copy()
    env["DK_PORT"] = str(PORT)
    env["DK_DIR"] = ROOT
    env["DK_DATA"] = os.path.join(tmp, "main.json")
    proc = subprocess.Popen([node, "server.js"], cwd=os.path.join(ROOT, "desktop"), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    try:
        ready = wait_ready(proc)
        if not ready or not ready.get("ok"):
            print("桌面端服务没起来：", ready)
            return 3

        src = open(SRC, encoding="utf-8").read()
        anchor = "})();\n</script>"
        if anchor not in src:
            print("找不到主 IIFE 结尾锚点")
            return 2
        i = src.rindex(anchor)
        probe = PROBE.replace("__BASE__", BASE).replace("__PORT__", str(PORT))
        page = os.path.join(tmp, "t.html")
        open(page, "w", encoding="utf-8").write(src[:i] + probe + src[i:])

        url = "file:///" + page.replace("\\", "/")
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                            "--no-default-browser-check", "--allow-file-access-from-files",
                            "--window-size=1280,1400", "--virtual-time-budget=25000",
                            "--user-data-dir=" + os.path.join(tmp, "prof"), "--dump-dom", url],
                           capture_output=True, timeout=240)
        dom = r.stdout.decode("utf-8", "replace")
        dom_ns = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
        mm = re.search(r'<pre id="__T">(.*?)</pre>', dom_ns, flags=re.S)
        if not mm:
            print("!! 没拿到测试输出，DOM 长度", len(dom))
            print(dom_ns[:1200])
            return 3
        data = json.loads(html.unescape(mm.group(1)))

        # Android 原生侧：配对状态持久化的两个方法确实存在（改了 Java 就得同步改这里）
        java_dir = os.path.join(ROOT, "android", "src", "com", "xiaotuji", "drugkeeper")
        bridge_src = open(os.path.join(java_dir, "Bridge.java"), encoding="utf-8").read()
        main_src = open(os.path.join(java_dir, "MainActivity.java"), encoding="utf-8").read()
        native_ok = ("saveSync" in bridge_src and "readSync" in bridge_src and
                     "void saveSync" in main_src and "String readSync" in main_src and
                     "SharedPreferences" in main_src and "MODE_PRIVATE" in main_src)
        data["cases"].append({"name": "★ 原生 Bridge/MainActivity 有 saveSync/readSync（SharedPreferences 持久化配对）",
                              "pass": native_ok, "got": "" if native_ok else "缺方法或没用 SharedPreferences"})

        fails = [c for c in data["cases"] if not c["pass"]]
        for c in data["cases"]:
            print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c["got"]))
        print("\n" + "-" * 60)
        print("通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))
        return 1 if fails else 0
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(0.4)
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
