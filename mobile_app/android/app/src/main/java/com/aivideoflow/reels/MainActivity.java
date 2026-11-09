package com.aivideoflow.reels;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * Standalone Android Native Activity for AI Video Flow Reels (.apk).
 * Starts the embedded Python mobile_backend on 127.0.0.1:5680 in a background
 * daemon thread and loads the mobile_app/www interface inside a hardware-accelerated WebView.
 */
public class MainActivity extends Activity {
    private static final int FILE_CHOOSER_RESULT_CODE = 1001;
    private WebView webView;
    private ValueCallback<Uri[]> uploadMessage;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 1. Start embedded Python AI Reels Engine (Chaquopy)
        startEmbeddedPythonServer();

        // 2. Initialize Fullscreen Hardware-Accelerated WebView
        webView = new WebView(this);
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url != null && (url.endsWith(".apk") || (!url.contains("127.0.0.1") && !url.contains("localhost") && !url.contains("razorpay")))) {
                    try {
                        Intent browserIntent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                        startActivity(browserIntent);
                        return true;
                    } catch (Exception ignored) {}
                }
                return false;
            }
        });
        webView.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) -> {
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                startActivity(i);
            } catch (Exception ignored) {}
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> filePathCallback, FileChooserParams fileChooserParams) {
                if (uploadMessage != null) {
                    uploadMessage.onReceiveValue(null);
                }
                uploadMessage = filePathCallback;
                Intent intent = fileChooserParams.createIntent();
                if (fileChooserParams.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(Intent.createChooser(intent, "Select Video Clips"), FILE_CHOOSER_RESULT_CODE);
                } catch (Exception e) {
                    uploadMessage = null;
                    return false;
                }
                return true;
            }
        });

        // Show dark studio splash immediately while embedded Python engine binds port 5680
        String splashHtml = "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            + "<style>body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;"
            + "background:#0b0d12;color:#eef1f7;font-family:-apple-system,BlinkMacSystemFont,sans-serif;}"
            + ".ring{width:38px;height:38px;border:3px solid #242b3a;border-top-color:#7c5cff;border-radius:50%;animation:s 0.8s linear infinite;margin-bottom:14px;}"
            + "@keyframes s{to{transform:rotate(360deg)}}h2{font-size:17px;margin:0 0 4px;}p{font-size:12.5px;color:#8e96aa;margin:0;}</style></head>"
            + "<body><div class='ring'></div><h2>AI Video Flow</h2><p>Starting AI Reels Studio…</p></body></html>";
        webView.loadDataWithBaseURL("http://127.0.0.1:5680", splashHtml, "text/html", "UTF-8", null);

        // Poll port 5680 and load the Studio UI as soon as the embedded server is ready
        new Thread(() -> {
            for (int attempt = 0; attempt < 60; attempt++) {
                try (java.net.Socket s = new java.net.Socket("127.0.0.1", 5680)) {
                    runOnUiThread(() -> webView.loadUrl("http://127.0.0.1:5680"));
                    return;
                } catch (Exception ignored) {
                    try { Thread.sleep(250); } catch (InterruptedException ie) { break; }
                }
            }
            runOnUiThread(() -> webView.loadUrl("file:///android_asset/www/index.html"));
        }, "AIReelsPortWaiter").start();
    }

    private void startEmbeddedPythonServer() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                Python py = Python.getInstance();
                PyObject backend = py.getModule("mobile_backend");
                backend.callAttr("run_server", "127.0.0.1", 5680, false);
            } catch (Exception e) {
                e.printStackTrace();
            }
        }, "AIReelsPythonServer").start();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_RESULT_CODE) {
            if (uploadMessage == null) return;
            Uri[] results = null;
            if (resultCode == Activity.RESULT_OK && data != null) {
                ClipData clipData = data.getClipData();
                if (clipData != null && clipData.getItemCount() > 0) {
                    results = new Uri[clipData.getItemCount()];
                    for (int i = 0; i < clipData.getItemCount(); i++) {
                        results[i] = clipData.getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                } else {
                    results = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
                }
            }
            uploadMessage.onReceiveValue(results);
            uploadMessage = null;
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
