package io.firststock.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.chaquo.python.Python;

/**
 * 휴대폰 앱의 전부. 파이썬 프로그램(PC 판과 같은 코드)을 이 휴대폰 안에서 켜고,
 * 그 화면(http://127.0.0.1:포트)을 WebView 로 띄운다. 바깥 주소(뉴스 원문 등)는 브라우저로 연다.
 */
public class MainActivity extends Activity {
    private static final String BACKGROUND = "#121416";
    private WebView web;
    private String home = "";
    private volatile int appPort = -1;      // 파이썬 화면이 켜진 포트. 켜지기 전에는 앱 안으로 여는 주소가 없다

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setStatusBarColor(Color.parseColor(BACKGROUND));
        getWindow().setNavigationBarColor(Color.parseColor(BACKGROUND));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor(BACKGROUND));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);         // 휴대폰 파일·다른 앱 자료는 화면에서 열 일이 없다
        settings.setAllowContentAccess(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                // 앱 안의 화면은 '이 앱의 포트' 만. 쿠키는 포트를 가리지 않아서, 같은 휴대폰의 다른 앱이
                // 127.0.0.1 의 다른 포트로 띄운 곳을 이 창에서 열면 화면 열쇠(쿠키)가 그쪽으로 간다.
                if ("http".equals(url.getScheme()) && "127.0.0.1".equals(url.getHost())
                        && url.getPort() == appPort && appPort > 0) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, url));
                } catch (Exception ignored) {
                    // 열 앱이 없으면 그냥 둔다
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // 무엇이 떴는지 기록에 남긴다(가상 휴대폰 검사가 읽는다). 주소의 열쇠는 남기지 않는다 — 경로만.
                String path = url == null ? "" : String.valueOf(Uri.parse(url).getPath());
                Log.i("FirstStock", "page " + path + " | " + view.getTitle());
            }
        });
        setContentView(web);
        web.loadDataWithBaseURL(null, page("First Stock 을 켜는 중…",
                "처음 한 번은 10~20초쯤 걸립니다."), "text/html", "utf-8", null);
        home = getFilesDir().getAbsolutePath();
        new Thread(this::boot, "boot").start();
    }

    private void boot() {
        try {
            // 받는 주소에는 이 앱만 아는 열쇠가 붙어 있다 — 같은 휴대폰의 다른 앱은 화면을 못 연다
            String url = Python.getInstance().getModule("stock_analysis.mobile")
                    .callAttr("start", home).toString();
            appPort = Uri.parse(url).getPort();
            runOnUiThread(() -> web.loadUrl(url));
        } catch (Exception e) {
            String reason = String.valueOf(e.getMessage());
            runOnUiThread(() -> web.loadDataWithBaseURL(null,
                    page("켜지 못했습니다", escape(reason)), "text/html", "utf-8", null));
        }
    }

    private static String escape(String text) {
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }

    private static String page(String title, String body) {
        return "<!doctype html><html lang=ko><head><meta charset=utf-8>"
                + "<meta name=viewport content='width=device-width,initial-scale=1'>"
                + "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:" + BACKGROUND
                + ";color:#e8e6e1;font:15px/1.6 sans-serif}main{max-width:420px;padding:24px}"
                + "h1{font-size:19px;color:#f0b648;margin:0 0 8px}p{color:#9aa3ad;white-space:pre-wrap;word-break:break-all}"
                + "</style></head><body><main><h1>" + title + "</h1><p>" + body + "</p></main></body></html>";
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            moveTaskToBack(true);       // 뒤로가기로 앱을 끄지 않는다 — 다시 열면 바로 이어서 본다
        }
    }
}
