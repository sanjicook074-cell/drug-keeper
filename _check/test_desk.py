# -*- coding: utf-8 -*-
"""
drug-keeper：桌面端「数据台」的接口自检。

用户 2026-11-20 的要求（原话大意）：
    「再做一个桌面版，桌面版和手机 APP、网页版互联，起同步数据的作用；
      三者在一个局域网里；桌面版还负责有更新了就发给手机端，让手机端自动更新。」

桌面端 = Electron 窗口 + 一个局域网 HTTP 服务（desktop/server.js）。窗口那层是给人看的，
**真正会被手机/网页调用的是 HTTP 接口**，所以这里直接起 node 把那套接口跑起来打。

钉这几条（都是「错了就会静默丢数据」的地方）：
  ① 手机网页（file://）能跨域访问 —— CORS 全开 + OPTIONS 预检要通，否则浏览器直接拦掉；
  ② 首次上传收得下，主档落盘是合法 JSON；
  ③ 之后传一份**更旧**的数据，不许覆盖主档 —— 得把主档发回去让它自己覆盖；
  ④ 覆盖前留 .bak 快照（同步着把数据弄丢是最不能接受的失败）；
  ⑤ 页面与安装包能取：/api/page 的字节 md5 必须等于 /api/page/meta 报的 md5，
     不然手机端会「以为更新了、其实下载到半截」；
  ⑥ 设备台账：谁来过、什么类型、几味药、最后什么时候。
"""
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = drug-keeper/
DESK = os.path.join(ROOT, "desktop")
PORT = 18730
BASE = "http://127.0.0.1:%d" % PORT
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

CASES = []


def ck(name, cond, got=""):
    CASES.append({"name": name, "pass": bool(cond), "got": got})


def check_desktop_dom():
    """真开一次无头 Chrome，往页面里塞一个假的 window.desk，看桌面专属那几条有没有生效。

    光做源码字符串检查不够：2026-11-20 就把「数据台设置」按钮的初始化写在了 IS_DESK
    的 const 之前，整份页面脚本 ReferenceError 直接挂掉，源码断言却全绿。
    """
    if not os.path.exists(CHROME):
        ck("桌面分支的浏览器实测（找不到 Chrome，跳过）", False, CHROME)
        return
    src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    stub = ('<script>window.desk={base:"http://127.0.0.1:8730",'
            'openHub:function(){window.__hub=1;}};</script>')
    src = src.replace("</head>", stub + "</head>", 1)
    probe = """
setTimeout(function(){
  hubRefresh();
  var b=document.querySelector('#btnDeskHub'), a=document.querySelector('#hubAddr');
  document.title='DESKTEST '+JSON.stringify({
    btn:getComputedStyle(b).display, addr:a.value, ro:a.readOnly,
    sep:document.querySelector('#hubSep').textContent,
    note:document.querySelector('#hubNote').textContent.slice(0,30)
  });
},60);
"""
    anchor = "})();\n</script>"      # 必须塞进页面脚本自己的 IIFE 里，hubRefresh 才可见
    i = src.rindex(anchor)
    page = src[:i] + probe + src[i:]
    tmp = tempfile.mkdtemp(prefix="dk_deskdom_")
    try:
        p = os.path.join(tmp, "sim.html")
        open(p, "w", encoding="utf-8").write(page)
        r = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
             "--virtual-time-budget=4000", "--dump-dom",
             "file:///" + p.replace("\\", "/")],
            capture_output=True, timeout=120)
        out = r.stdout.decode("utf-8", "replace")
        m = re.search(r"<title>DESKTEST (\{.*?\})</title>", out, re.S)
        d = json.loads(html.unescape(m.group(1))) if m else {}
        ck("★ 桌面环境里「数据台设置」按钮真的显示出来（不是留在 DOM 里藏着）",
           d.get("btn") not in (None, "none"), d.get("btn"))
        ck("★ 同步地址自动锁到本机服务，不用手填",
           d.get("addr") == "http://127.0.0.1:8730" and d.get("ro") is True,
           str(d.get("addr")) + " / 只读=" + str(d.get("ro")))
        ck("桌面端提示语改说「已连上本机」，分隔标题改说「与手机端」",
           "已连上本机" in (d.get("note") or "") and "与手机端" in (d.get("sep") or ""),
           str(d.get("note")) + " / " + str(d.get("sep")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def req(path, body=None, method=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(BASE + path, data=data, headers=h,
                               method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, dict((k.lower(), v) for k, v in resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict((k.lower(), v) for k, v in e.headers.items()), e.read()


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
        print("没找到 node，跳过桌面端自检")
        return 2
    if not os.path.isfile(os.path.join(DESK, "server.js")):
        print("desktop/server.js 不见了")
        return 2

    tmp = tempfile.mkdtemp(prefix="dk_desk_")
    data_file = os.path.join(tmp, "main.json")
    env = os.environ.copy()
    env["DK_PORT"] = str(PORT)
    env["DK_DIR"] = ROOT
    env["DK_DATA"] = data_file

    proc = subprocess.Popen([node, "server.js"], cwd=DESK, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    try:
        ready = wait_ready(proc)
        ck("★ 局域网服务起得来（端口 %d）" % PORT, ready and ready.get("ok"), ready)

        st, hd, body = req("/api/ping")
        ping = json.loads(body or b"{}")
        ck("★ /api/ping 认得自己是药品管家", ping.get("app") == "drug-keeper" and ping.get("ok"), ping.get("app"))

        # ---- 服务端时钟（2026-11-20 修「两端差几小时导致同步不上」）----
        srv_now = int(ping.get("serverNow") or 0)
        ck("★ 服务端下发 serverNow，供两端校准时钟（修同步不上的根因）",
           abs(srv_now - int(time.time() * 1000)) < 5000, srv_now)

        # ---- 配对码（2026-11-20：手机只输 6 位码就能连）----
        code = str(ping.get("pairCode") or "")
        ck("★ /api/ping 回一个 6 位数字配对码", re.fullmatch(r"\d{6}", code) is not None, code)
        st, _, body = req("/api/state")
        state_code = str(json.loads(body or b"{}").get("pairCode") or "")
        ck("配对码在 /api/state 里也有，且与 ping 一致（桌面窗口显示的就是它）",
           state_code == code and code != "", state_code)

        # UDP 广播应答：用正确的码问，服务端要在 PAIR_PORT 回一段含局域网地址的 JSON
        import socket as _sock
        udp = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        udp.settimeout(2.5)
        try:
            udp.sendto(code.encode("utf-8"), ("127.0.0.1", PORT + 1))   # UDP 端口 = HTTP 端口 + 1
            reply, _ = udp.recvfrom(2048)
            info = json.loads(reply.decode("utf-8", "replace"))
            ck("★ 输对配对码，UDP 广播能问到电脑地址（手机靠它自动连）",
               bool(info.get("addrs")) and int(info.get("port", 0)) == PORT, info)
        except Exception as ex:
            ck("★ 输对配对码，UDP 广播能问到电脑地址（手机靠它自动连）", False, repr(ex))
        finally:
            udp.close()
        # 错码不该得到应答（否则同网段谁都能连）
        udp2 = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        udp2.settimeout(0.8)
        try:
            udp2.sendto(b"000000" if code != "000000" else b"999999", ("127.0.0.1", PORT + 1))
            udp2.recvfrom(2048)
            ck("配对码不对时不回地址（同网段乱连会被挡）", False, "竟然回了")
        except _sock.timeout:
            ck("配对码不对时不回地址（同网段乱连会被挡）", True, "超时，正确")
        except Exception as ex:
            ck("配对码不对时不回地址（同网段乱连会被挡）", True, repr(ex))
        finally:
            udp2.close()

        ck("★ 跨域头全开（file:// 页面靠它才连得上）",
           hd.get("access-control-allow-origin") == "*", hd.get("access-control-allow-origin"))

        st, hd, _ = req("/api/ping", method="OPTIONS")
        ck("OPTIONS 预检回 204 且带 CORS（不然浏览器里 POST 直接被拦）",
           st == 204 and hd.get("access-control-allow-origin") == "*",
           str(st) + " / " + str(hd.get("access-control-allow-origin")))

        # ---- 页面版本：meta 与真实字节必须对得上 ----
        disk = open(os.path.join(ROOT, "index.html"), "rb").read()
        disk_md5 = hashlib.md5(disk).hexdigest()
        st, _, body = req("/api/page/meta")
        meta = json.loads(body or b"{}")
        ck("★ /api/page/meta 报的 md5 就是磁盘上那份 index.html",
           meta.get("md5") == disk_md5, (meta.get("md5") or "")[:12] + " vs " + disk_md5[:12])
        st, hd, body = req("/api/page")
        ck("★ /api/page 取回来的整页，字节 md5 和 meta 一致（不会发半截）",
           hashlib.md5(body).hexdigest() == disk_md5 and "text/html" in (hd.get("content-type") or ""),
           "%d 字节 / %s" % (len(body), (hd.get("content-type") or "")[:24]))

        # ---- 安装包 ----
        apk_path = os.path.join(ROOT, "android", "drug-keeper.apk")
        st, hd, body = req("/api/apk")
        if os.path.isfile(apk_path):
            apk_size = os.path.getsize(apk_path)
            ck("★ /api/apk 能把安装包整个发出去", st == 200 and len(body) == apk_size,
               "%d / %d 字节" % (len(body), apk_size))
        else:
            ck("还没打包 APK 时 /api/apk 老实回 404，不是空文件", st == 404, st)

        # ---- 同步：先上传 ----
        data_str = json.dumps({"people": [{"id": "a", "name": "我", "meds": [{}, {}]}],
                               "currentId": "a"}, ensure_ascii=False)
        phone = {"deviceId": "phone-1", "deviceName": "小米手机", "kind": "phone",
                 "clientTime": 1795000000000, "data": data_str, "settings": "{}",
                 "people": 1, "meds": 2}
        st, _, body = req("/api/sync", phone)
        r1 = json.loads(body or b"{}")
        ck("★ 手机第一次上传，主档收下了", r1.get("action") == "pushed", r1.get("action"))
        ck("主档能解析出「1 个人 2 味药」",
           (r1.get("summary") or {}).get("people") == 1 and (r1.get("summary") or {}).get("meds") == 2,
           r1.get("summary"))
        ck("设备台账里出现了这台手机（类型/药数/时间都在）",
           bool(r1.get("devices")) and r1["devices"][0].get("kind") == "phone"
           and r1["devices"][0].get("meds") == 2, (r1.get("devices") or [{}])[0].get("name"))

        ck("主档真的落盘了，而且是合法 JSON",
           os.path.isfile(data_file) and isinstance(json.load(open(data_file, encoding="utf-8")), dict),
           os.path.basename(data_file))

        # ---- 同步：再传一份更旧的 —— 不许覆盖 ----
        older = dict(phone)
        older["deviceId"] = "web-1"
        older["deviceName"] = "旧网页"
        older["kind"] = "web"
        older["clientTime"] = 1794000000000       # 比上面早
        older["data"] = json.dumps({"people": [{"id": "b", "name": "旧的", "meds": []}],
                                    "currentId": "b"}, ensure_ascii=False)
        older["people"] = 1
        older["meds"] = 0
        st, _, body = req("/api/sync", older)
        r2 = json.loads(body or b"{}")
        ck("★ 传上来一份更旧的数据 → 不覆盖，把主档发回去让它自己改",
           r2.get("action") == "pulled", r2.get("action"))
        ck("发回去的就是那份「2 味药」的主档，没被旧数据顶掉",
           json.loads(r2.get("data") or "{}").get("people", [{}])[0].get("name") == "我",
           (r2.get("data") or "")[:40])
        ck("旧设备只是登记了一下，不占主档",
           (r2.get("summary") or {}).get("meds") == 2, r2.get("summary"))

        # ---- 覆盖时要留快照 ----
        newer = dict(phone)
        newer["clientTime"] = 1796000000000
        newer["deviceName"] = "小米手机"
        newer["data"] = json.dumps({"people": [{"id": "a", "name": "我", "meds": [{}, {}, {}]}],
                                    "currentId": "a"}, ensure_ascii=False)
        newer["meds"] = 3
        req("/api/sync", newer)
        bak = data_file.replace(".json", ".bak.json")
        ck("★ 覆盖前留了一份 .bak 快照（改错了还能捞回来）",
           os.path.isfile(bak) and json.load(open(bak, encoding="utf-8")).get("data") == data_str,
           os.path.basename(bak))
        st, _, body = req("/api/state")
        st2 = json.loads(body or b"{}")
        ck("新数据生效：主档变成 3 味", (st2.get("summary") or {}).get("meds") == 3,
           (st2.get("summary") or {}).get("meds"))

        # ---- force：不管时间戳，以发起的这端为准 ----
        #   ★ 这条是补出来的。第一版少了 force，结果主档时间戳只要比手机新
        #   （差几秒都算），手机点「上传到电脑」就被静默退回主档，页面却显示"已存到电脑"。
        forced = dict(phone)
        forced["deviceId"] = "web-2"
        forced["deviceName"] = "网页版"
        forced["kind"] = "web"
        forced["clientTime"] = 1790000000000        # 比主档旧一大截
        forced["data"] = json.dumps({"people": [{"id": "z", "name": "强推的", "meds": [{}]}],
                                     "currentId": "z"}, ensure_ascii=False)
        forced["people"] = 1
        forced["meds"] = 1
        forced["force"] = True
        st, _, body = req("/api/sync", forced)
        r3 = json.loads(body or b"{}")
        ck("★ 带 force 时不看时间戳，直接以这端为准（否则「上传」会被静默退回）",
           r3.get("action") == "pushed" and
           json.loads(r3.get("data") or "{}").get("people", [{}])[0].get("name") == "强推的",
           r3.get("action"))

        # ---- 首页（手机浏览器手动测连通用） ----
        st, hd, body = req("/")
        ck("根地址有个小说明页，能直接在手机浏览器里确认「连上了」",
           st == 200 and "药品管家".encode("utf-8") in body, str(st))

        # ---- 桌面形态（2026-11-20 用户重新定位：桌面版 = 完整网页版 + 同步；
        #      数据台退成设置窗口，网页只作展示）----
        # 这一组是源码级体检：Electron 窗口没有无头 Chrome 那么好驱动，
        # 但「主窗口加载错了文件」这种回潮必须当场抓住。
        with open(os.path.join(DESK, "main.js"), encoding="utf-8") as f:
            mjs = f.read()
        with open(os.path.join(DESK, "preload.js"), encoding="utf-8") as f:
            pjs = f.read()
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            page = f.read()
        ck("★ 主窗口加载项目里的 index.html（完整应用，不是只剩同步面板）",
           "win.loadFile(path.join(cfg.projectDir, 'index.html'))" in mjs
           and "win.loadFile(path.join(APP_DIR, 'ui', 'index.html'))" not in mjs)
        ck("★ 数据台退成单独的设置窗口，界面按钮能打开它",
           "function createHubWindow()" in mjs
           and "ipcMain.handle('open-hub'" in mjs
           and "hubWin.loadFile(path.join(APP_DIR, 'ui', 'index.html'))" in mjs)
        ck("预加载注入本机服务地址 + 打开数据台的方法",
           "base: 'http://127.0.0.1:' + PORT" in pjs and "openHub" in pjs)
        ck("★ 页面认出桌面环境：同步默认本机，设备登记成「电脑」而不是「网页版」",
           "const IS_DESK" in page and "const DESK_BASE" in page
           and "kind: IS_DESK ? 'desktop'" in page
           and "deviceName: IS_DESK ? '电脑'" in page)
        ck("桌面专属的「数据台设置」按钮默认隐藏，只在桌面显示",
           'id="btnDeskHub"' in page and "classList.remove('hidden')" in page)

        # ---- 2026-11-20 晚：图标统一 + 启动脚本编码（用户反馈乱码 / 不是内部命令）----
        with open(os.path.join(DESK, "ui", "index.html"), encoding="utf-8") as f:
            hubui = f.read()
        ico = os.path.join(DESK, "yao-clear.ico")
        n_icon = mjs.count("icon: path.join(APP_DIR, 'yao-clear.ico')")
        raw_ico = open(ico, "rb").read() if os.path.exists(ico) else b""
        n_sizes = int.from_bytes(raw_ico[4:6], "little") if len(raw_ico) >= 6 else 0
        widths = [raw_ico[6 + i * 16] for i in range(n_sizes)]
        ck("★ 两个窗口都用蓝底「药」字图标（标题栏 / 任务栏统一）",
           n_icon == 2 and n_sizes >= 10 and 48 in widths and 128 in widths,
           "main.js %d 处, ico 尺寸数 %d" % (n_icon, n_sizes))
        ck("★ 图标含 48/64 等原生尺寸，不是只有 16x16 被系统放大（桌面发糊的根因）",
           48 in widths and 64 in widths and 32 in widths,
           "尺寸数 %d, 宽度 %s" % (n_sizes, sorted(set(widths))))
        ck("数据台右上角状态标签换成「药」字标，不再只是绿点",
           '<i class="mini">药</i>' in hubui and ".pill .mini" in hubui
           and 'class="dot"' not in hubui)
        for bat in ("launch.bat", "启动数据台.bat"):
            raw = open(os.path.join(DESK, bat), "rb").read()
            nonascii = sum(1 for x in raw if x > 127)
            ck("%s 纯英文 + CRLF（防 cmd 乱码 /「不是内部命令」）" % bat,
               nonascii == 0 and b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"),
               "bytes=%d nonascii=%d crlf=%d" % (len(raw), nonascii, raw.count(b"\r\n")))
        check_desktop_dom()

        fails = [c for c in CASES if not c["pass"]]
        for c in CASES:
            print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c["got"]))
        print("\n" + "-" * 60)
        print("通过 %d / %d" % (len(CASES) - len(fails), len(CASES)))
        return 1 if fails else 0
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(0.4)
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
