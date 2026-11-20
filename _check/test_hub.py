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
    ck('两个动作是「上传到电脑」「从电脑拉取」',
       /上传到电脑/.test((q('#btnHubUp') || {}).textContent || '') &&
       /从电脑拉取/.test((q('#btnHubDown') || {}).textContent || ''), '');

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
