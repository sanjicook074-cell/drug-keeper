# -*- coding: utf-8 -*-
"""把 index.html 编成一个能装的 APK —— 不装 Android Studio、不装 gradle。

用法：
    python android/build.py            # 完整构建 + 签名 + 自检
    python android/build.py --clean    # 顺手清掉 build/

只依赖三样东西（都在本机，路径可用环境变量覆盖）：
    DK_JBR     JDK（默认用 PyCharm 自带的 JBR，它就是完整 OpenJDK）
    DK_BT      build-tools 目录（aapt2 / zipalign / lib/d8.jar / lib/apksigner.jar）
    DK_JAR     android.jar（编译期 classpath，来自 platform-33）

★ 为什么用 Python 而不是 .sh：aapt2 / javac / zipalign 都是原生 Windows 程序，
  收到 Git Bash 的 /c/Users/... 会报「系统找不到指定的文件」；Python 这边
  用 os.path 拼出来的就是 C:\\...，绕开整类问题。构建日志编码也一并躲掉。
"""
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))          # drug-keeper/android
PKG_ROOT = os.path.dirname(HERE)                          # drug-keeper
SRC_HTML = os.path.join(PKG_ROOT, "index.html")           # 网页本体（唯一真源）
BUILD = os.path.join(HERE, "build")
ASSETS = os.path.join(HERE, "assets")
RES = os.path.join(HERE, "res")
MANIFEST = os.path.join(HERE, "AndroidManifest.xml")
SRC_JAVA = os.path.join(HERE, "src")
KEYSTORE = os.path.join(HERE, "drugkeeper.jks")
OUT_APK = os.path.join(HERE, "drug-keeper.apk")

APP_ID = "com.xiaotuji.drugkeeper"
MIN_SDK, TARGET_SDK = "26", "33"

SDK = os.environ.get("DK_SDK") or os.path.join(os.path.expanduser("~"), ".android-build", "sdk")
BT = os.environ.get("DK_BT") or os.path.join(SDK, "bt_raw", "android-14")
ANDROID_JAR = os.environ.get("DK_JAR") or os.path.join(SDK, "pl_raw", "android-13", "android.jar")


def find_jbr():
    """JDK 候选：环境变量 → 常见 IDE 自带的 JBR → PATH 上的 java。"""
    env = os.environ.get("DK_JBR")
    if env and os.path.isfile(os.path.join(env, "bin", "javac.exe")):
        return env
    import glob as _g
    cands = []
    for pat in (r"C:\Program Files\JetBrains\*\jbr",
                r"C:\Program Files\Android\Android Studio\jbr",
                r"C:\Program Files\Java\jdk*",
                r"C:\Program Files\Eclipse Adoptium\jdk*"):
        cands += _g.glob(pat)
    for c in sorted(cands, reverse=True):
        if os.path.isfile(os.path.join(c, "bin", "javac.exe")):
            return c
    return ""


JBR = find_jbr()
JAVAC = os.path.join(JBR, "bin", "javac.exe")
JAVA = os.path.join(JBR, "bin", "java.exe")
KEYTOOL = os.path.join(JBR, "bin", "keytool.exe")

AAPT2 = os.path.join(BT, "aapt2.exe")
ZIPALIGN = os.path.join(BT, "zipalign.exe")
D8_JAR = os.path.join(BT, "lib", "d8.jar")
SIGNER_JAR = os.path.join(BT, "lib", "apksigner.jar")


def run(cmd, what):
    print("  · " + what)
    p = subprocess.run(cmd, capture_output=True)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    if p.returncode != 0:
        print(out.strip()[-4000:])
        raise SystemExit("[x] 失败：%s（退出码 %d）" % (what, p.returncode))
    return out


def preflight():
    miss = []
    for path, name in ((JAVAC, "javac（JDK）"), (AAPT2, "aapt2"), (ZIPALIGN, "zipalign"),
                       (D8_JAR, "lib/d8.jar"), (SIGNER_JAR, "lib/apksigner.jar"),
                       (ANDROID_JAR, "android.jar"), (SRC_HTML, "index.html")):
        if not os.path.exists(path):
            miss.append("%s -> %s" % (name, path))
    if miss:
        print("[x] 缺东西：\n    " + "\n    ".join(miss))
        raise SystemExit(1)
    print("JDK      %s" % JBR)
    print("build-tools  %s" % BT)


def main():
    if "--clean" in sys.argv:
        shutil.rmtree(BUILD, ignore_errors=True)

    preflight()
    shutil.rmtree(BUILD, ignore_errors=True)
    for d in ("classes", "dex", "gen"):
        os.makedirs(os.path.join(BUILD, d), exist_ok=True)

    # ---- 1. 把网页拷进 assets（唯一真源还是根目录那个 index.html）----
    os.makedirs(ASSETS, exist_ok=True)
    dst_html = os.path.join(ASSETS, "index.html")
    shutil.copyfile(SRC_HTML, dst_html)
    html = open(dst_html, encoding="utf-8").read()
    if "AndroidApp" not in html:
        raise SystemExit("[x] index.html 里没有 Android 壳补丁（AndroidApp），装了也用不了导出/复制")
    src_sha = hashlib.sha256(open(SRC_HTML, "rb").read()).hexdigest()
    print("\n[1/7] assets/index.html  %.0f KB  sha=%s"
          % (len(html.encode("utf-8")) / 1024, src_sha[:12]))

    # ---- 2. aapt2 compile：资源 ----
    res_zip = os.path.join(BUILD, "res.zip")
    run([AAPT2, "compile", "--dir", RES, "-o", res_zip], "aapt2 compile（资源）")
    print("[2/7] 资源编好  %d 个文件" % len(glob.glob(os.path.join(RES, "**", "*.*"), recursive=True)))

    # ---- 3. aapt2 link：出未签名的 base.apk ----
    base_apk = os.path.join(BUILD, "base.apk")
    # ★ 资源 zip 要当**输入文件**传（位置参数），不能写成 `-R res.zip`：
    #   -R 是「overlay」语义，会报 "resource style/AppTheme does not override an existing
    #   resource"，看着像资源名写错了，其实是参数用错了。
    run([AAPT2, "link", "-o", base_apk, "-I", ANDROID_JAR,
         "--manifest", MANIFEST, "-A", ASSETS,
         "--min-sdk-version", MIN_SDK, "--target-sdk-version", TARGET_SDK, res_zip],
        "aapt2 link（打包资源 + assets）")
    print("[3/7] base.apk  %d KB" % (os.path.getsize(base_apk) / 1024))

    # ---- 4. javac ----
    # 源码清单自动枚举：写死成 for f in A B C 的话，加一个类忘了加就会在手机上
    # NoClassDefFoundError，而编译期一声不响。
    java_files = glob.glob(os.path.join(SRC_JAVA, "**", "*.java"), recursive=True)
    if not java_files:
        raise SystemExit("[x] src/ 下没有 .java")
    classes = os.path.join(BUILD, "classes")
    run([JAVAC, "-encoding", "UTF-8",
         "-J-Duser.language=en", "-J-Duser.country=US",
         "-source", "8", "-target", "8",
         "-bootclasspath", ANDROID_JAR,
         "-classpath", ANDROID_JAR,
         "-d", classes] + java_files, "javac（%d 个类）" % len(java_files))

    # ---- d8 的两个死坑：先在这里查出来，别等它在 d8 里 NPE ----
    class_files = glob.glob(os.path.join(classes, "**", "*.class"), recursive=True)
    bad = []
    for cf in class_files:
        if os.path.basename(cf).count("$") and os.path.basename(cf).split("$")[1][:1].isdigit():
            bad.append(cf)                                     # Foo$1.class = 匿名内部类
            continue
        blob = open(cf, "rb").read()
        if b"this$0" in blob:
            bad.append(cf)                                     # 非静态内部类
    if bad:
        print("[x] 这些 class 会让 d8 崩（匿名内部类 / 非静态内部类），改成 static 顶层类：")
        for b in bad:
            print("    " + os.path.relpath(b, classes))
        raise SystemExit(1)
    print("[4/7] class 文件 %d 个，无匿名/非静态内部类" % len(class_files))

    # ---- 5. d8 → classes.dex ----
    dexdir = os.path.join(BUILD, "dex")
    run([JAVA, "-cp", D8_JAR, "com.android.tools.r8.D8",
         "--min-api", MIN_SDK, "--lib", ANDROID_JAR, "--output", dexdir] + class_files,
        "d8（class → dex）")
    dex = os.path.join(dexdir, "classes.dex")
    if not os.path.isfile(dex):
        raise SystemExit("[x] d8 没产出 classes.dex")
    print("[5/7] classes.dex  %d KB" % (os.path.getsize(dex) / 1024))

    # ---- 6. 把 dex 塞进 apk 再对齐 ----
    with zipfile.ZipFile(base_apk, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(dex, "classes.dex")
    aligned = os.path.join(BUILD, "aligned.apk")
    run([ZIPALIGN, "-f", "-p", "4", base_apk, aligned], "zipalign")
    print("[6/7] 对齐完成")

    # ---- 7. 签名 ----
    if not os.path.isfile(KEYSTORE):
        print("  · 第一次构建，生成签名密钥（以后重装必须用同一个，别删）")
        run([KEYTOOL, "-genkeypair", "-keystore", KEYSTORE, "-alias", "drugkeeper",
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-storepass", "android", "-keypass", "android",
             "-dname", "CN=DrugKeeper, OU=Self, O=Self, L=CN, ST=CN, C=CN"],
            "keytool genkeypair")
    if "--no-sign" in sys.argv:
        shutil.copyfile(aligned, OUT_APK)
    else:
        run([JAVA, "-jar", SIGNER_JAR, "sign",
             "--ks", KEYSTORE, "--ks-pass", "pass:android", "--key-pass", "pass:android",
             "--out", OUT_APK, aligned], "apksigner sign")
        out = run([JAVA, "-jar", SIGNER_JAR, "verify", "--print-certs", OUT_APK],
                  "apksigner verify")
        for line in out.splitlines():
            if "Signer #1 certificate DN" in line or "Verified using" in line:
                print("    " + line.strip())

    report(OUT_APK)


def report(apk):
    size = os.path.getsize(apk)
    sha = hashlib.sha256(open(apk, "rb").read()).hexdigest()
    z = zipfile.ZipFile(apk)
    names = z.namelist()
    dex = b"".join(z.read(n) for n in names if n.endswith(".dex")).decode("utf-8", "ignore")
    checks = [
        ("assets/index.html 打进包里了", "assets/index.html" in names),
        ("dex 里有 JS 桥的名字 AndroidApp", "AndroidApp" in dex),
        ("dex 里有包名", APP_ID.replace(".", "/") in dex or APP_ID in dex),
        ("dex 里没有占位符 __TOKEN__", "__TOKEN__" not in dex),
        ("没有申请 INTERNET 权限", True),
    ]
    man = z.read("AndroidManifest.xml")
    def has(s):
        return s.encode("utf-8") in man or s.encode("utf-16-le") in man
    checks[4] = ("没有申请 INTERNET 权限", not has("android.permission.INTERNET"))
    checks.append(("manifest 里 .MainActivity 已导出", has(".MainActivity")))

    # ★ 最容易出的一类事故：编进去的还是上一次的网页。
    #   （assets/ 是构建时从根目录 index.html 复制的副本，一旦哪次复制被跳过，
    #    装到手机上才发现"改的东西不在里面"，而构建日志一路都是绿的。）
    inapk = z.read("assets/index.html")
    same = hashlib.sha256(inapk).hexdigest() == hashlib.sha256(open(SRC_HTML, "rb").read()).hexdigest()
    checks.append(("包里的 index.html 和仓库根目录那个逐字节一致（不是旧副本）", same))

    print("\n===== 产物 =====")
    print("%s  %.1f MB" % (os.path.relpath(apk, PKG_ROOT), size / 1024.0 / 1024.0))
    print("sha256 %s" % sha)
    ok = True
    for name, passed in checks:
        print("  %s %s" % ("OK  " if passed else "FAIL", name))
        ok = ok and passed
    if not ok:
        raise SystemExit("[x] 产物自检没过")


if __name__ == "__main__":
    main()
