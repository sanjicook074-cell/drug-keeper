package com.xiaotuji.drugkeeper;

import android.net.Uri;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;

/**
 * 只为一件事存在：让页面里那个藏着的 `<input type="file" id="fileImport">` 能用。
 * WebView 默认**什么都不做**，用户点「导入」不会弹任何东西 —— 看起来就是"按钮没反应"。
 *
 * ★ 这里只持有 ValueCallback<Uri[]> 并原样转交，**不 implements 它**。
 *   implements 泛型接口 = d8 必崩（技能坑 1 第二条）。
 */
class ChooserClient extends WebChromeClient {

    private final MainActivity act;

    ChooserClient(MainActivity a) {
        act = a;
    }

    @Override
    public boolean onShowFileChooser(WebView view,
                                     ValueCallback<Uri[]> callback,
                                     FileChooserParams params) {
        act.openFileChooser(callback);
        return true;
    }
}
