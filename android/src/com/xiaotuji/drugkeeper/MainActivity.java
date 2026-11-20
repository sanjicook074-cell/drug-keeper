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

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.HttpURLConnection;
import java.net.URL;

import org.json.JSONArray;
import org.json.JSONObject;

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

        ensurePage();
        web.loadUrl(Uri.fromFile(pageFile).toString());
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

    /* ---------------- 页面热更新（2026-11-20）----------------
       用户要求「桌面版有更新了就发给手机端，让手机端自动更新」。
       所以页面不再直接跑 assets 里那份 —— assets 是只读的，改不了；改成先在应用私有目录放一份，
       以后桌面「数据台」发了新版本就整篇替换。

       ★ 换页面之前先把当前数据写一份 backup.json。file:// 的 localStorage 在 WebView 里
         是跨路径共享的，正常不会丢；但这条只值几百字节，留着当兜底 —— 真丢了不至于一片空白。
       ★ 写文件一律「先写 .new 再 rename」：别让断电留下半篇 HTML，那会让 App 下次直接白屏。 */
    private File pageDir;
    private File pageFile;

    private void ensurePage(){
        pageDir = new File(getFilesDir(), "page");
        if(!pageDir.exists()) pageDir.mkdirs();
        pageFile = new File(pageDir, "index.html");
        if(!pageFile.exists() || pageFile.length() < 4096){
            copyAsset("index.html", pageFile);
        }
    }

    private boolean copyAsset(String name, File dst){
        try {
            InputStream in = getAssets().open(name);
            File tmp = new File(dst.getPath() + ".new");
            FileOutputStream out = new FileOutputStream(tmp);
            byte[] buf = new byte[8192];
            int n;
            while((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.flush();
            out.close();
            in.close();
            if(dst.exists()) dst.delete();
            return tmp.renameTo(dst);
        } catch(Exception e){
            toast("页面准备失败：" + e.getMessage());
            return false;
        }
    }

    /** 当前跑着的那份页面的 md5 —— 页面拿它跟桌面上的比，判断有没有新版本 */
    String pageMd5(){
        try {
            if(pageFile == null) return "";
            InputStream in = new FileInputStream(pageFile);
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("MD5");
            byte[] buf = new byte[8192];
            int n;
            while((n = in.read(buf)) > 0) md.update(buf, 0, n);
            in.close();
            byte[] d = md.digest();
            StringBuilder sb = new StringBuilder();
            for(int i = 0; i < d.length; i++){
                String h = Integer.toHexString(0xff & d[i]);
                if(h.length() == 1) sb.append('0');
                sb.append(h);
            }
            return sb.toString();
        } catch(Exception e){ return ""; }
    }

    /** 换页面：备份数据 → 原子替换 → 重载 */
    void installPage(String html, String data, String settings){
        if(html == null || html.length() < 4096){
            toast("新页面不完整，这次先不换");
            return;
        }
        try {
            if(data != null && data.length() > 0){
                JSONObject o = new JSONObject();
                o.put("data", data);
                o.put("settings", settings == null ? "" : settings);
                File bak = new File(pageDir, "backup.json");
                FileOutputStream fo = new FileOutputStream(bak);
                fo.write(o.toString().getBytes("UTF-8"));
                fo.flush();
                fo.close();
            }
            File tmp = new File(pageDir, "index.html.new");
            FileOutputStream out = new FileOutputStream(tmp);
            out.write(html.getBytes("UTF-8"));
            out.flush();
            out.close();
            if(pageFile.exists()) pageFile.delete();
            if(!tmp.renameTo(pageFile)){
                toast("新页面写不进去");
                return;
            }
        } catch(Exception e){
            toast("更新失败：" + e.getMessage());
            return;
        }
        runOnUiThread(new ReloadTask(this));
    }

    /** 页面启动时如果发现 localStorage 是空的，就来这儿要上一份 */
    String readBackup(){
        try {
            File bak = new File(pageDir, "backup.json");
            if(!bak.exists()) return "";
            InputStream in = new FileInputStream(bak);
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while((n = in.read(buf)) > 0) bo.write(buf, 0, n);
            in.close();
            return bo.toString("UTF-8");
        } catch(Exception e){ return ""; }
    }

    String appVersion(){
        try { return getPackageManager().getPackageInfo(getPackageName(), 0).versionName; }
        catch(Exception e){ return ""; }
    }

    /**
     * 输配对码找电脑：往局域网广播这个码，只有码对得上的桌面服务才回自己的地址。
     * 页面 JS 拿到的是 "http://192.168.x.x:8730" 这种完整地址；失败返回空串。
     * ★ 这个方法不在主线程跑（Bridge 由 JavaBridge 线程调用），最多等 2.5 秒。
     */
    String findHub(String code){
        DatagramSocket s = null;
        try {
            String c = code == null ? "" : code.trim();
            if(!c.matches("\\d{6}")) return "";
            s = new DatagramSocket();
            s.setBroadcast(true);
            s.setSoTimeout(2500);
            byte[] ask = c.getBytes("UTF-8");
            s.send(new DatagramPacket(ask, ask.length,
                    InetAddress.getByName("255.255.255.255"), 8731));
            byte[] buf = new byte[2048];
            DatagramPacket reply = new DatagramPacket(buf, buf.length);
            s.receive(reply);
            String txt = new String(reply.getData(), reply.getOffset(), reply.getLength(), "UTF-8");
            JSONObject o = new JSONObject(txt);
            JSONArray a = o.getJSONArray("addrs");
            if(a.length() < 1) return "";
            int port = o.optInt("port", 8730);
            // 电脑上可能有多个网卡，广播回包里带了全部地址。逐个 ping 一下，
            // 用真正能连通的那个 —— 只取第一个的话，虚拟网卡地址会让手机一直连不上。
            for(int i = 0; i < a.length(); i++){
                String host = a.getString(i);
                String base = "http://" + host + ":" + port;
                if(pingOk(base)) return base;
            }
            // 都 ping 不通也返回第一个，让上层给出「连不上」的具体提示
            return "http://" + a.getString(0) + ":" + port;
        } catch(Exception e){
            return "";
        } finally {
            if(s != null) s.close();
        }
    }

    /** 短超时地探一下这个地址是不是药品管家服务，通就返回 true */
    private boolean pingOk(String base){
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/ping").openConnection();
            c.setConnectTimeout(600);
            c.setReadTimeout(600);
            c.setRequestMethod("GET");
            return c.getResponseCode() == 200;
        } catch(Exception e){
            return false;
        } finally {
            if(c != null) c.disconnect();
        }
    }

    /** 下载安装包：直接交给系统浏览器/下载器，装的时候系统还会再问一次 */
    void openUrl(String url){
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch(Exception e){
            toast("没有能打开链接的程序");
        }
    }

    /** ★ 顶层 static 类 —— 和 ToastTask 同理，d8 不接受匿名内部类 */
    static class ReloadTask implements Runnable {
        private final MainActivity act;
        ReloadTask(MainActivity a){ act = a; }
        public void run(){
            if(act.web != null) act.web.reload();
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
