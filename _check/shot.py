# -*- coding: utf-8 -*-
"""
生成 docs/ 里的四张成品截图（空状态 / 主界面 / 「已经开药」弹窗 / 「批量调存量」弹窗）。

★ 演示数据由本脚本**临时注入**到一份临时副本里，绝不写回 index.html ——
  开源版程序内已经没有任何内置示例数据（2026-09-28 用户要求抹掉）。
  注入内容只用虚构的通用药名与用法，不含任何病情描述。
"""
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # drug-keeper/
SRC = os.path.join(ROOT, "index.html")
OUT = os.path.join(ROOT, "docs")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

DEMO_JS = r"""
/* ===== 仅截图用：临时注入演示数据 ===== */
(function(){
  var agoD = function(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); };
  var demo = [
    { name:'苯磺酸氨氯地平片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:7, note:'早饭后服' },
    { name:'阿托伐他汀钙片', spec:'20mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:21, stockDate:agoD(18), rxMax:14, note:'晚饭后服' },
    { name:'盐酸二甲双胍缓释片', spec:'0.5g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
      stockBase:60, stockDate:agoD(20), note:'随餐服' },
    { name:'阿司匹林肠溶片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:90, stockDate:agoD(10), note:'空腹服' },
    { name:'依洛尤单抗注射液', spec:'140mg/支', perDose:1, unit:'支', cycleDays:14, timesPerCycle:1,
      stockBase:2, stockDate:agoD(20), rxMax:2, note:'皮下注射，每 2 周 1 次' }
  ];
  demo.forEach(function(d){ d.id = uid(); });
  setMeds(demo);
  var who = curPerson();
  if(who) who.note = '药快吃完了，想续方。';
  save(); render();
  __SHOT_HOOK__
})();
"""

HOOKS = {
    "main": "",
    "disp": "openDisp();",
    "bulk": "openBulk();",
}


def shoot(profile, url, out_path, size):
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--hide-scrollbars", "--window-size=" + size, "--virtual-time-budget=5000",
           "--user-data-dir=" + profile, "--screenshot=" + out_path, url]
    subprocess.run(cmd, capture_output=True, timeout=180)


def main():
    src = open(SRC, encoding="utf-8").read()
    anchor = "})();\n</script>"
    i = src.rindex(anchor)

    tmpdir = tempfile.mkdtemp(prefix="dk_shot_")
    os.makedirs(OUT, exist_ok=True)

    # 1) 空状态：直接用原文件，隔离 profile 保证 localStorage 为空
    shoot(os.path.join(tmpdir, "p0"), "file:///" + SRC.replace("\\", "/"),
          os.path.join(OUT, "screenshot-empty.png"), "1240,760")
    print("空状态 -> docs/screenshot-empty.png")

    # 2) 主界面 / 3) 开药弹窗：注入演示数据的临时副本
    for name, hook in HOOKS.items():
        page = os.path.join(tmpdir, name + ".html")
        open(page, "w", encoding="utf-8").write(src[:i] + DEMO_JS.replace("__SHOT_HOOK__", hook) + src[i:])
        url = "file:///" + page.replace("\\", "/")
        out = os.path.join(OUT, "screenshot-%s.png" % name)
        shoot(os.path.join(tmpdir, "p_" + name), url, out, "1240,1120")
        print("%s -> %s" % (name, os.path.relpath(out, ROOT)))

    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
