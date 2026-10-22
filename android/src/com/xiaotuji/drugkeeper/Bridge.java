package com.xiaotuji.drugkeeper;

import android.webkit.JavascriptInterface;

/**
 * 页面能调到的全部原生能力，就这四个方法。
 *
 * ★ 这些方法**不在主线程**上执行（WebView 的 JavaBridge 线程），
 *   所以这里只做转发，凡是碰界面的（Toast）都由 MainActivity 转回主线程。
 * ★ 名字要和 index.html 末尾那段「Android 壳补丁」里调的一模一样，
 *   改这里必须同步改那边（页面上有窗口对象判断，浏览器里这段不会跑）。
 */
class Bridge {

    private final MainActivity act;

    Bridge(MainActivity a) {
        act = a;
    }

    /** 导出备份 / 下载 .txt 用：把内容落到本机文件 */
    @JavascriptInterface
    public void saveText(String name, String text) {
        act.saveText(name, text);
    }

    /** 「复制给医生」「复制药名」用：写系统剪贴板 */
    @JavascriptInterface
    public void copy(String text) {
        act.copy(text);
    }

    /** 页面同步「有没有弹窗开着」，返回键据此决定先关弹窗还是退出 */
    @JavascriptInterface
    public void setOverlay(boolean open) {
        act.overlayOpen = open;
    }

    @JavascriptInterface
    public void toast(String msg) {
        act.toast(msg);
    }
}
