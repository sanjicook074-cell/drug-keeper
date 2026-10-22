# -*- coding: utf-8 -*-
"""Android 壳补丁：把「WebView 里才走的那几条路」在无头 Chrome 里真跑一遍。

为什么要单独一套：APK 编出来只能证明「打得开」，
证明不了「导出备份真的落盘」「复制给医生真的进剪贴板」「返回键真的先关弹窗」——
这三件事全在 index.html 末尾那段壳补丁里，而它在浏览器里是**整段不执行**的，
平时五套自检一条都覆盖不到。

做法：复制 index.html，在 `<body>` 后插一个假的 `window.AndroidApp`，
再把探针插进主 IIFE 末尾 —— 补丁就会像在真壳里一样跑起来，调用记在那个假对象上。

★ 反面也要验：**没有 AndroidApp 时补丁必须一声不响地跳过**（网页版靠这条不变味）。
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "index.html")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ★ 这个桩必须在页面主脚本**之前**执行，否则补丁那一句 `if(!A) return;` 直接跳过。
STUB = """<script>
window.__CALLS = { save: [], copy: [], overlay: [], toast: [] };
window.AndroidApp = {
  saveText: function(n, t){ window.__CALLS.save.push({ name: n, text: t }); },
  copy: function(t){ window.__CALLS.copy.push(t); },
  setOverlay: function(b){ window.__CALLS.overlay.push(!!b); },
  toast: function(m){ window.__CALLS.toast.push(m); }
};
</script>
"""

PROBE = r"""
(async function(){
  var R = { cases: [] };
  function q(s){ return document.querySelector(s); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: String(got) }); }
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  async function waitFor(fn, ms){
    var t = 0;
    while(t < (ms || 1500)){
      try { if(fn()) return true; } catch(e){}
      await sleep(25); t += 25;
    }
    return false;
  }
  var C = window.__CALLS;
  var topOpen = function(){
    var ds = Array.prototype.slice.call(document.querySelectorAll('dialog[open]'));
    return ds.length ? ds[ds.length - 1] : null;
  };

  try {
  /* ---- 演示数据：维C 只剩 1 片（已用完），维B2 还有 60 片（充足）----
     这样一边能进「要开的药」/「全家偏少」，另一边不该出现，两边都好验。 */
  var demo = [
    { name:'维生素C片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:1, stockDate:todayStr(), rxMax:30, note:'早饭后服' },
    { name:'维生素B2片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:60, stockDate:todayStr(), rxMax:30 }
  ];
  demo.forEach(function(d){ d.id = uid(); });
  setMeds(demo); save(); render();
  await sleep(20);

  /* ---- 0. 补丁装上了 ---- */
  ck('补丁生效：<html> 上多了 .in-app 类',
     document.documentElement.classList.contains('in-app'),
     document.documentElement.className);
  ck('补丁挂了返回键入口 window.__appBack',
     typeof window.__appBack === 'function', typeof window.__appBack);

  /* ---- 1. 导出备份：要真的调用原生写文件 ---- */
  C.save.length = 0;
  q('#btnExport').click();
  await waitFor(function(){ return C.save.length > 0; });
  var s0 = C.save[0] || { name: '', text: '' };
  ck('导出备份走原生写文件（不再用 blob 下载）', C.save.length === 1, C.save.length + ' 次');
  ck('导出的文件名带「药品库存备份」和 .json', /药品库存备份.*\.json/.test(s0.name), s0.name);
  var parsed = null;
  try { parsed = JSON.parse(s0.text); } catch(e){}
  ck('导出内容是一份能解析的整包备份（含 people 数组）',
     !!parsed && Array.isArray(parsed.people) && parsed.app === 'drug-keeper',
     parsed ? ('people=' + parsed.people.length) : '解析失败');

  /* ---- 2. 下载 .txt 也走同一条路 ---- */
  C.save.length = 0;
  q('#btnDownload').click();
  await waitFor(function(){ return C.save.length > 0; });
  ck('下载 .txt 也走原生写文件', C.save.length === 1, C.save.length + ' 次');
  ck('下载的是一份 .txt', /\.txt$/.test((C.save[0] || {}).name || ''), (C.save[0] || {}).name);

  /* ---- 3. 复制给医生：走系统剪贴板 ---- */
  C.copy.length = 0;
  q('#btnCopy').click();
  await waitFor(function(){ return C.copy.length > 0; });
  var clip = C.copy[0] || '';
  ck('「复制给医生」走原生剪贴板', C.copy.length === 1, C.copy.length + ' 次');
  ck('复制出去的是要开的药清单（非空且带药名）',
     clip.length > 8 && clip.indexOf('维生素C片') >= 0,
     clip.slice(0, 26).replace(/\n/g, ' '));

  /* ---- 4. 全家偏少的「复制药名」也走剪贴板 ---- */
  C.copy.length = 0;
  openFam();
  await sleep(40);
  q('#btnFamCopy').click();
  await waitFor(function(){ return C.copy.length > 0; });
  var famTxt = C.copy[0] || '';
  ck('「复制药名」走原生剪贴板', C.copy.length === 1, C.copy.length + ' 次');
  ck('复制出去的是偏少的那些（含维C，不含充足的维B2）',
     famTxt.indexOf('维生素C片') >= 0 && famTxt.indexOf('维生素B2片') < 0,
     famTxt.slice(0, 30).replace(/\n/g, ' '));
  q('#dlgFam').close();
  await sleep(30);

  /* ---- 5. 弹窗开合要同步给原生（返回键靠它判断） ---- */
  C.overlay.length = 0;
  openEditor(meds[1].id);
  await waitFor(function(){ return C.overlay.length > 0 && C.overlay[C.overlay.length - 1] === true; });
  ck('弹窗打开时告诉原生「现在有弹窗」',
     C.overlay.length > 0 && C.overlay[C.overlay.length - 1] === true,
     JSON.stringify(C.overlay));

  C.overlay.length = 0;
  q('#btnCancel').click();
  await waitFor(function(){ return C.overlay.length > 0 && C.overlay[C.overlay.length - 1] === false; });
  ck('弹窗关掉后同步回 false',
     C.overlay.length > 0 && C.overlay[C.overlay.length - 1] === false,
     JSON.stringify(C.overlay));

  /* ---- 6. 返回键：先关弹窗，不退出 ---- */
  q('#btnSettings').click();
  await sleep(60);
  var hadOpen = !!topOpen();
  var back1 = window.__appBack();
  await sleep(60);
  ck('返回键在有弹窗时「用掉了」这一次（返回 true）', hadOpen && back1 === true, hadOpen + '/' + back1);
  ck('返回键把那个弹窗关掉了', !topOpen(), topOpen() ? topOpen().id : '(没有开着的弹窗)');
  var back2 = window.__appBack();
  ck('没有弹窗时返回键交还给系统（返回 false）', back2 === false, back2);

  /* ---- 7. 返回键关「确认框」= 按 Esc = 取消，不能让 await 悬着 ---- */
  var before = meds.length;
  var pend = delMed(meds[0].id);        // 弹窗在调用那一刻就同步打开了
  var opened = !!topOpen();
  var back3 = window.__appBack();
  var res = await Promise.race([pend, sleep(800).then(function(){ return 'TIMEOUT'; })]);
  ck('删除确认框能被返回键关掉', opened && back3 === true, opened + '/' + back3);
  ck('关掉等于「取消」：Promise 有结果，不是悬着的', res !== 'TIMEOUT', String(res));
  ck('取消之后药没被删掉', meds.length === before, meds.length + '/' + before);
  ck('确认框确实关了', !topOpen(), topOpen() ? topOpen().id : '(已关)');

  /* ---- 8. 打印按钮在壳里藏掉（WebView 没有打印服务） ---- */
  var pb = q('#btnPrint');
  ck('打印按钮在壳里隐藏（点了也没反应，不如不显示）',
     !!pb && pb.style.display === 'none', pb ? pb.style.display : '(没有)');

  } catch(err){
    ck('探针自己没炸', false, (err && (err.stack || err.message)) || err);
  }

  var pre = document.createElement('pre');
  pre.id = '__T';
  pre.textContent = JSON.stringify(R);
  document.body.appendChild(pre);
})();
"""


def run_page(src, tmpdir):
    page = os.path.join(tmpdir, "inapp.html")
    body = src.replace("<body>", "<body>\n" + STUB, 1)
    anchor = "})();\n</script>"
    i = body.rindex(anchor)
    open(page, "w", encoding="utf-8").write(body[:i] + PROBE + body[i:])
    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--window-size=420,900",
           "--virtual-time-budget=9000",
           "--user-data-dir=" + os.path.join(tmpdir, "prof"), "--dump-dom", url]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    dom = r.stdout.decode("utf-8", "replace")
    dom_ns = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
    mm = re.search(r'<pre id="__T">(.*?)</pre>', dom_ns, flags=re.S)
    if not mm:
        print("!! 没拿到测试输出，DOM 长度 %d" % len(dom))
        print(dom_ns[:1500])
        return None
    return json.loads(html.unescape(mm.group(1)))


def main():
    src = open(SRC, encoding="utf-8").read()
    if "})();\n</script>" not in src:
        print("找不到主 IIFE 结尾锚点")
        return 2

    tmpdir = tempfile.mkdtemp(prefix="dk_app_")
    data = run_page(src, tmpdir)
    if data is None:
        return 3
    fails = [c for c in data["cases"] if not c["pass"]]
    for c in data["cases"]:
        print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c.get("got")))
    print("\n" + "-" * 60)
    print("壳内补丁：通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))

    # ★ 反面：浏览器里（没有 AndroidApp）补丁必须整段不生效。这三条是静态判据，
    #   不用再起一次 Chrome（再跑一遍要等 9 秒虚拟时间，而这里看的是源码形状）。
    print("\n浏览器侧（同一个文件，只是没有 AndroidApp）：")
    neg = [
        ("补丁第一步就是 `if(!A) return`：浏览器里立刻退出",
         bool(re.search(r"const A = window\.AndroidApp;\s*\n\s*if\(!A\) return;", src))),
        ("只在壳里挂一个全局 window.__appBack，没有别的全局污染",
         src.count("window.__appBack") == 1),
        ("downloadFile / fallbackCopy 各自只被替换一次（网页版仍是原实现）",
         len(re.findall(r"^\s*downloadFile = function", src, flags=re.M)) == 1
         and len(re.findall(r"^\s*fallbackCopy = function", src, flags=re.M)) == 1),
    ]
    for name, ok in neg:
        print(("  OK  " if ok else "  FAIL") + "  " + name)
        if not ok:
            fails.append({"name": name, "pass": False})

    total = len(data["cases"]) + len(neg)
    print("\n合计 %d / %d" % (total - len(fails), total))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
