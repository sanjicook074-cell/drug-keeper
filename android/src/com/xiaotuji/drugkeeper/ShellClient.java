package com.xiaotuji.drugkeeper;

import android.content.Intent;
import android.net.Uri;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * 页面本身不会跳转（单文件、单页），这里只做两件事：
 *   ① 万一有外链（http/https），交给系统浏览器打开，别在壳里跑 —— 壳没有地址栏，
 *      在壳里点开就再也回不来了；
 *   ② 页面加载完把「弹窗状态」复位一次。
 *
 * ★ 顶层类 + 显式传 Activity：命名内部类如果是**非静态**的，d8 一样会崩（技能坑 1 第三条）。
 */
class ShellClient extends WebViewClient {

    private final MainActivity act;

    ShellClient(MainActivity a) {
        act = a;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
        Uri uri = req == null ? null : req.getUrl();
        if (uri == null) return false;
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
        if (scheme.equals("http") || scheme.equals("https") || scheme.equals("mailto")) {
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, uri);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                act.startActivity(i);
            } catch (Exception e) {
                act.toast("手机上打不开这个链接");
            }
            return true;
        }
        return false;
    }

    @Override
    public void onPageFinished(WebView view, String url) {
        // 冷启动时序：onCreate 跑在页面 load 之前，那时 JS 钩子还没挂上。
        // 页面加载完再复位一次，免得上一轮的 true 残留下来把返回键吃掉。
        act.overlayOpen = false;
    }
}
