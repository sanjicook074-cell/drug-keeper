# -*- coding: utf-8 -*-
"""
生成 docs/ 里的成品截图（空状态 / 主界面 / 「已经开药」/「批量调存量」/「编辑药品」/「又买了药」）。

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
    { name:'维生素C片', spec:'100mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:14, stockDate:agoD(13), rxMax:7, note:'早饭后服' },
    { name:'维生素B2片', spec:'5mg', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:21, stockDate:agoD(18), rxMax:14, note:'晚饭后服' },
    { name:'葡萄糖酸钙片', spec:'0.5g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:2,
      stockBase:60, stockDate:agoD(20), rxMax:30, note:'随餐服' },
    { name:'健胃消食片', spec:'0.8g', perDose:1, unit:'片', cycleDays:1, timesPerCycle:1,
      stockBase:90, stockDate:agoD(10), rxMax:30, note:'空腹服' },
    { name:'维生素B12注射液', spec:'0.5mg/支', perDose:1, unit:'支', cycleDays:14, timesPerCycle:1,
      stockBase:2, stockDate:agoD(20), rxMax:2, note:'皮下注射，每 2 周 1 次' },
    /* ★ 2026-10-22 起药分两类，截图里必须有「备用药」那一栏 —— 否则 docs/ 还是旧版的样子，
       README 就成了"图和事实对不上"。这两味的 timesPerCycle = 0 / kind = 'prn'：
       不按天数推算、不进续方清单、卡片右上角写的是**余量**不是天数。 */
    { name:'布洛芬缓释胶囊', spec:'0.3g*20', perDose:1, unit:'粒', cycleDays:1, timesPerCycle:0,
      stockBase:16, stockDate:agoD(40), kind:'prn', note:'疼的时候吃' },
    { name:'蒙脱石散', spec:'3g*10', perDose:3, unit:'袋', cycleDays:1, timesPerCycle:0,
      stockBase:4, stockDate:agoD(90), kind:'prn' }
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
    # ★ 批量调存量（2026-10-12 改版）：列出**全部**药品、可勾选、顺序同主页、按档位着色。
    #   这里临时多塞一味「没设用法」的药，为的是把「勾选框禁用 + 没法按天算 + 底部那行说明」拍进来
    #   （只加在这一张里，别的截图和主页不受影响）。
    "bulk": "var d = meds.concat([{ id:uid(), name:'维生素E软胶囊', spec:'400IU', perDose:1, unit:'粒',"
            " cycleDays:1, timesPerCycle:0, stockBase:30, stockDate:todayStr() }]);"
            " setMeds(d); save(); render(); openBulk();",
    # ★ 编辑弹窗：拍的是「余量」那格 —— 预填的就是卡片上那个数（当天推算值），
    #   拍下来是为了核对弹窗与卡片同框一致（用户 2026-10-11 报过这两处对不上）。
    #   挑「每天 2 片」那味：卡片写「余量约 20 片」，弹窗里预填的也该是 20。
    #   2026-10-13 起这张还兼一份差事：把「哪几格必填、哪两格选填」拍明白
    #   （带红 `*` 的 8 格 + 规格/备注上的「选填」小签 + 底部那行说明）。
    #   所以演示数据里**每一味都补上了 rxMax** —— 老数据缺这格会显示成空白提示要补填，
    #   那是迁移态，不适合当成品图。
    "edit": "openEditor(meds[2].id);",
    # ★ 「备用药」的编辑弹窗（2026-10-22 新增）—— 和上面那张对照着看，最省口舌：
    #   同一张表单，选了备用药之后「每几天 / 几次」和「开药限制」整块收起，
    #   只剩「一次吃多少 / 单位 / 余量 / 余量日期」。拍下来是为了钉住"备用药不用填那些"。
    #   挑 meds[5] = 布洛芬缓释胶囊（演示数据里第 6 位、kind=prn）。
    "prnedit": "openEditor(meds[5].id);",
    # ★ 「又买了药」现在是中间弹窗（原来走浏览器原生 prompt，2026-10-11 用户要求改掉）。
    #   2026-10-12 用户要求把那行实时对照删掉，所以这里只填数、不再调 renderNumCalc。
    #   顺手把输入框填成 28，拍的是「标题 + 说明 + 输入框 + 按钮」的干净形态。
    "restock": "restockMed(meds[2].id);"
               " document.querySelector('#numVal').value = '28';",
    # ★ 校准余量：同一个弹窗的另一种模式，同样已无实时对照行（2026-10-12）。
    #   拍它是因为用户就是在这个弹窗里提的「不用 0.25 这一档」——留一张可核 step=1。
    "calib": "calibMed(meds[2].id);",
    # ★ 底方录入（2026-10-12 新增；2026-10-13 起页面**不再认照片**，只接助手给的链接 / 手头文字）：
    #   把底方原文解析后摆进弹窗，拍「每行都算好了够吃几天」。弹窗顶上那块是「照片发给助手」的说明。
    #   ⚠️ 这里必须照**用户真实的操作顺序**来：`openRx()` 开弹窗 → `rxAddRows()` 把解析结果并进去
    #   （粘文字那条路的 `btnRxParse` 就是这么走的）。早先图省事直接 `openRx(解析结果)`，
    #   结果把「这份底方是谁的」那一块也拍进来了 —— 真实粘贴流程里它根本不显示
    #   （`rxWho` 只在"有底方／有助手来源"时才出）。**成品图和事实对不上就算错**。
    "rx": "openRx(); rxAddRows(rxParseText(['维生素B6片(Ⅰ)[华康][10mg*30]  1瓶  19.88  10mg  每日一次  口服',"
          " '阿莫西林胶囊(康贝)[0.25g*8]  2盒  6.26  0.5g  每日2次  口服',"
          " '麝香壮骨膏[40mg*6贴]  5盒  57.87  1贴  每日一次  外用',"
          " '维生素B12注射液[甲钴胺][1ml:0.5mg*]  1盒  15.60  0.5mg  每两周一次  皮下注射'].join('\\n')));",
    # ★ 底方「对账」（2026-10-13 新增）：「对账依据」这件事**只对助手给的链接**提要求 ——
    #   用户自己粘文字时没有"谁读的"这回事，不该冒出来质问"没人替你核过"。
    #   这里模拟助手那条路：2 张底方、逐张对账 → 弹窗顶上多一条「助手交了对账 ✓」。
    #   拍它是因为教训：**写在说明里的规矩会被跳过，摆到界面上的校验才跳不过去**。
    #   姓名必须用页面里**已存在**的「我」：换个陌生名字会弹「要不要新建」的 confirm，
    #   headless 下没人应答 → 截图直接卡死（第一版就栽在这）。
    "rxaudit": "openRx(rxParseText(['维生素B6片(Ⅰ)[华康][10mg*30]  1瓶  19.88  10mg  每日一次  口服',"
               " '阿莫西林胶囊(康贝)[0.25g*8]  2盒  6.26  0.5g  每日2次  口服',"
               " '麝香壮骨膏[40mg*6贴]  5盒  57.87  1贴  每日一次  外用',"
               " '维生素B12注射液[甲钴胺][1ml:0.5mg*]  1盒  15.60  0.5mg  每两周一次  皮下注射'].join('\\n')),"
               " '助手', { name:'我', sex:'女', age:'58' },"
               " [ {i:1,n:2,amount:32.40,sum:32.40}, {i:2,n:2,amount:304.95,sum:304.95} ]);",
    # ★ 批量填用法（2026-10-13 新增）：药名进来之后点「批量填用法」跳的那个窗口。
    #   上面一条「统一设置」+ 下面逐味微调，拍的是「吃法和天数一次填完」的样子。
    #   先 openRx 把行摆好、再 openUse 叠在上面 —— 和用户真实操作顺序一致。
    "rxuse": "openRx(); rxAddRows(rxParseText(['维生素B6片(Ⅰ)[华康][10mg*30]  1瓶  19.88  10mg  每日一次  口服',"
             " '阿莫西林胶囊(康贝)[0.25g*8]  2盒  6.26  0.5g  每日2次  口服',"
             " '麝香壮骨膏[40mg*6贴]  5盒  57.87  1贴  每日一次  外用'].join('\\n')));"
             " openUse();",
}


# 个别图要比默认画布高一点：底方那两张顶上多挂了「这份底方是谁的」/「助手交了对账」两块，
# 默认 1120 会把底部「加入药箱」按钮切掉（切掉的成品图不算成品）。
# 2026-10-22 起演示数据多了 2 味备用药（主页多一栏标题 + 2 张卡片），
# main / bulk / edit 三张也要加高，否则「备用药」那一栏会被裁在画面外。
SIZE = {"rx": "1240,1240", "rxaudit": "1240,1320", "rxuse": "1240,1240",
        "main": "1240,1960", "bulk": "1240,1560", "edit": "1240,1300",
        "prnedit": "1240,1140"}


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
        # 底方那几张顶上多两块（「是谁的」+「对账」），1120 高会把底部按钮切掉
        shoot(os.path.join(tmpdir, "p_" + name), url, out, SIZE.get(name, "1240,1120"))
        print("%s -> %s" % (name, os.path.relpath(out, ROOT)))

    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
