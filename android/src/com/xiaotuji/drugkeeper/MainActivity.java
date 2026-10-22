package com.xiaotuji.drugkeeper;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

/**
 * 唯一的 Activity：把 index.html 装进 WebView 跑起来。
 *
 * ★ 这个壳能成立，靠的是页面自己**已经把浏览器会挡掉的三件事处理好**：
 *   ① 导出备份 / 下载 .txt：WebView 不认 `blob:` + `a.download`，
 *      页面在 app 模式下改调 AndroidApp.saveText() 落到「下载」目录；
 *   ② 导入：`<input type=file>` 在 WebView 里要 onShowFileChooser 才弹选择器；
 *   ③ 复制：`file://` 是不安全上下文，navigator.clipboard 用不了，
 *      execCommand 也不稳，改调 AndroidApp.copy() 走系统剪贴板。
 *
 * ★ d8 的老坑（见 android-apk-handmade-build 技能）：**一个匿名内部类都不许写**，
 *   命名内部类也必须 static，否则 d8 在「编译成 dex」这一步直接 NPE。
 *   Toast 要回主线程，所以用了顶层 Runnable 类 ToastTask，而不是 lambda / 匿名类。
 */
public class MainActivity extends Activity {

    private static final int REQ_FILE = 1001;

    private WebView web;

    /** onShowFileChooser 交过来的回调，等用户选完文件再回填 */
    private ValueCallback<Uri[]> pendingFile;

    /** 页面上有没有开着的弹窗（由 JS 侧同步过来），返回键先关它 */
    volatile boolean overlayOpen = false;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // ★ localStorage 全靠这一句，漏了页面就是空的
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(false);
        s.setSupportZoom(false);               // 壳里不要双指缩放，像 App 不像网页
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setTextZoom(100);                    // 不跟系统字体缩放跑，避免布局被撑坏
        if (Build.VERSION.SDK_INT >= 21) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        }

        web.setBackgroundColor(0xFFF4F5F7);    // 和页面 --bg 一致，滚动到顶/底不露白边
        web.setOverScrollMode(WebView.OVER_SCROLL_NEVER);
        web.setHorizontalScrollBarEnabled(false);
        web.addJavascriptInterface(new Bridge(this), "AndroidApp");
        web.setWebViewClient(new ShellClient(this));
        web.setWebChromeClient(new ChooserClient(this));

        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebView.setWebContentsDebuggingEnabled(true);   // 自用 App：留着 chrome://inspect 能查

        web.loadUrl("file:///android_asset/index.html");
    }

    /* ---------------- 返回键：先关弹窗，再退出 ---------------- */

    @Override
    public void onBackPressed() {
        if (web != null && overlayOpen) {
            // ★ 不用 evaluateJavascript 取返回值：它的回调类型是 ValueCallback<String>，
            //   泛型接口一旦 implements 就会让 d8 崩（技能里坑 1 的第二条）。
            //   改成 JS 侧主动把「有没有弹窗」同步过来，这里只读那个 boolean。
            web.loadUrl("javascript:window.__appBack&&window.__appBack()");
            return;
        }
        super.onBackPressed();
    }

    /* ---------------- 导入：选择文件 ---------------- */

    void openFileChooser(ValueCallback<Uri[]> cb) {
        if (pendingFile != null) {
            pendingFile.onReceiveValue(null);      // 上一个还没回，先结掉，否则那个 input 永远等
            pendingFile = null;
        }
        pendingFile = cb;
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");                     // 有的机型按 .json 过滤会把文件藏起来
        try {
            startActivityForResult(Intent.createChooser(intent, "选择备份文件"), REQ_FILE);
        } catch (Exception e) {
            pendingFile = null;
            toast("没有找到文件管理器");
        }
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        if (req == REQ_FILE) {
            if (pendingFile != null) {
                pendingFile.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(result, data));
                pendingFile = null;
            }
            return;
        }
        super.onActivityResult(req, result, data);
    }

    /* ---------------- 给 JS 用的三件事 ---------------- */

    /** 写文件。API 29+ 走 MediaStore 落进「下载」目录（不用任何权限）；更老的落进应用目录。 */
    String saveText(String name, String text) {
        String safe = safeName(name);
        final byte[] bytes;
        try {
            bytes = (text == null ? "" : text).getBytes("UTF-8");
        } catch (Exception e) {
            toast("保存失败：编码出错");
            return "";
        }
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues cv = new ContentValues();
                cv.put(MediaStore.Downloads.DISPLAY_NAME, safe);
                cv.put(MediaStore.Downloads.MIME_TYPE, mimeOf(safe));
                cv.put(MediaStore.Downloads.IS_PENDING, 1);
                Uri uri = getContentResolver()
                        .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                if (uri == null) throw new Exception("系统没给文件位置");
                OutputStream os = getContentResolver().openOutputStream(uri);
                if (os == null) throw new Exception("写不进去");
                os.write(bytes);
                os.flush();
                os.close();
                cv.clear();
                cv.put(MediaStore.Downloads.IS_PENDING, 0);
                getContentResolver().update(uri, cv, null, null);
                toast("已存到「下载」：" + safe);
                return "downloads/" + safe;
            }
            File dir = getExternalFilesDir("导出");
            if (dir == null) dir = getFilesDir();
            if (!dir.exists()) dir.mkdirs();
            File f = new File(dir, safe);
            FileOutputStream fo = new FileOutputStream(f);
            fo.write(bytes);
            fo.flush();
            fo.close();
            toast("已存到应用目录：" + f.getName());
            return f.getAbsolutePath();
        } catch (Exception e) {
            toast("保存失败：" + e.getMessage());
            return "";
        }
    }

    void copy(String text) {
        try {
            ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            if (cm != null) {
                cm.setPrimaryClip(ClipData.newPlainText("药品管家", text == null ? "" : text));
            }
        } catch (Exception e) {
            toast("复制失败");
        }
    }

    void toast(String msg) {
        runOnUiThread(new ToastTask(this, msg));
    }

    /** ★ 顶层 static 类：既不是匿名类、也不是非静态内部类，d8 才不会崩 */
    static class ToastTask implements Runnable {
        private final MainActivity act;
        private final String msg;

        ToastTask(MainActivity a, String m) {
            act = a;
            msg = m;
        }

        public void run() {
            Toast.makeText(act.getApplicationContext(), msg, Toast.LENGTH_SHORT).show();
        }
    }

    /* ---------------- 小工具 ---------------- */

    private static String safeName(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.length() == 0) s = "药品管家导出.txt";
        char[] bad = new char[]{'/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t'};
        for (int i = 0; i < bad.length; i++) s = s.replace(bad[i], '_');
        return s;
    }

    private static String mimeOf(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".json")) return "application/json";
        if (n.endsWith(".txt")) return "text/plain";
        return "application/octet-stream";
    }

    /* ---------------- 生命周期 ---------------- */

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            ViewGroup parent = (ViewGroup) web.getParent();
            if (parent != null) parent.removeView(web);
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
