package dev.ccremote.pager.web

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.provider.Settings
import android.view.View
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.net.toUri
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import dev.ccremote.pager.BuildConfig
import dev.ccremote.pager.data.ServerEndpoint
import dev.ccremote.pager.data.isPrivateOrLocalIpLiteral
import org.json.JSONObject

class SecureWebViewController(
    private val activity: Activity,
    initialEndpoint: ServerEndpoint?,
    private val onBridgeMessage: (String) -> Unit,
    private val onPageProblem: (String?) -> Unit,
    private val fileChooser: FileChooser,
) {
    val webView: WebView = WebView(activity)
    private var endpoint = initialEndpoint
    private var bridgeInstalled = false
    private var chatVisible = true
    private val refreshCompositorAfterReveal = requiresCompositorRefreshAfterReveal(
        WebViewCompat.getCurrentWebViewPackage(activity)?.versionName,
    )

    init {
        configureWebView()
        // A null endpoint means first launch: the app has no server configured
        // yet, so the WebView stays blank until the user enters one.
        endpoint?.let { installBridge(it) }
        endpoint?.let { webView.loadUrl(it.url) }
    }

    fun reconfigure(next: ServerEndpoint?) {
        if (next == endpoint) return
        endpoint = next
        if (bridgeInstalled
            && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
        ) {
            WebViewCompat.removeWebMessageListener(webView, BRIDGE_OBJECT)
            bridgeInstalled = false
        }
        if (next == null) {
            webView.stopLoading()
            return
        }
        installBridge(next)
        webView.loadUrl(next.url)
    }

    fun sendCommand(raw: String) {
        val quoted = JSONObject.quote(raw)
        webView.post {
            webView.evaluateJavascript(
                "(function(m){var f=window.__CC_REMOTE_NATIVE_RECEIVE__;" +
                    "if(typeof f==='function'){f(m);}})($quoted);",
                null,
            )
        }
    }

    fun setChatVisible(visible: Boolean) {
        val revealing = visible && !chatVisible
        chatVisible = visible
        if (!revealing) return
        webView.post {
            webView.onResume()
            webView.requestLayout()
            webView.postInvalidateOnAnimation()
            if (refreshCompositorAfterReveal) {
                // Chromium 90 on the target phone can keep the overlay-era
                // hardware frame. Rebuild only the layer first; a full reload
                // would tear down the authenticated WebSocket on every card tap.
                webView.setLayerType(View.LAYER_TYPE_SOFTWARE, null)
                webView.postOnAnimation {
                    webView.setLayerType(View.LAYER_TYPE_NONE, null)
                    webView.requestLayout()
                    webView.postInvalidateOnAnimation()
                }
            }
            verifyLiveDocumentAfterReveal()
        }
    }

    private fun verifyLiveDocumentAfterReveal() {
        webView.evaluateJavascript(
            "typeof window.__CC_REMOTE_NATIVE_RECEIVE__ === 'function'",
        ) { result ->
            if (chatVisible && result != "true") {
                // The page really was discarded or never completed loading.
                // This is the bounded recovery fallback, not normal navigation.
                webView.reload()
            }
        }
    }

    fun onResume() {
        webView.onResume()
        webView.evaluateJavascript(
            "window.dispatchEvent(new Event('ccremote-native-resume'))",
            null,
        )
    }

    fun onPause() {
        webView.onPause()
    }

    fun destroy() {
        if (bridgeInstalled
            && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
        ) {
            WebViewCompat.removeWebMessageListener(webView, BRIDGE_OBJECT)
        }
        webView.stopLoading()
        webView.webChromeClient = null
        webView.webViewClient = WebViewClient()
        webView.destroy()
    }

    @Suppress("SetJavaScriptEnabled", "DEPRECATION")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = false
            allowFileAccess = false
            allowContentAccess = false
            allowFileAccessFromFileURLs = false
            allowUniversalAccessFromFileURLs = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            mediaPlaybackRequiresUserGesture = false
            setSupportMultipleWindows(false)
            javaScriptCanOpenWindowsAutomatically = false
            safeBrowsingEnabled = true
            userAgentString = "$userAgentString CCRemoteNativePager/${BuildConfig.VERSION_NAME}"
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(webView, false)
        }
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean {
                if (isAllowedNavigation(request.url)) return false
                runCatching {
                    activity.startActivity(Intent(Intent.ACTION_VIEW, request.url))
                }
                return true
            }

            override fun shouldInterceptRequest(
                view: WebView,
                request: WebResourceRequest,
            ): WebResourceResponse? {
                // WebView-level enforcement of the cleartext policy: every
                // resource load (including subresources) must be HTTPS or a
                // private/local HTTP IP. Public HTTP and unknown schemes are
                // blocked here even if some script tries to load them.
                if (!isAllowedResource(request.url)) {
                    return WebResourceResponse(
                        "text/plain",
                        "utf-8",
                        403,
                        "Blocked by cc-remote cleartext policy",
                        emptyMap(),
                        null,
                    )
                }
                return null
            }

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                onPageProblem(null)
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError,
            ) {
                if (request.isForMainFrame) {
                    onPageProblem(error.description?.toString()?.take(240)
                        ?: "页面加载失败")
                }
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams,
            ): Boolean = fileChooser.launch(filePathCallback, fileChooserParams)
        }
    }

    private fun installBridge(value: ServerEndpoint) {
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(
                webView,
                BRIDGE_OBJECT,
                setOf(value.origin),
            ) { _, message, sourceOrigin, isMainFrame, _ ->
                if (!isMainFrame || sourceOrigin.toString() != endpoint?.origin) {
                    return@addWebMessageListener
                }
                message.data?.let(onBridgeMessage)
            }
            bridgeInstalled = true
        } else {
            onPageProblem("Android System WebView 版本过低，请更新后重试")
        }
    }

    private fun isAllowedNavigation(uri: Uri): Boolean {
        val current = endpoint ?: return false
        val port = when {
            uri.port != -1 -> uri.port
            uri.scheme == "https" -> 443
            else -> 80
        }
        val targetOrigin = buildString {
            append(uri.scheme)
            append("://")
            append(uri.host)
            val defaultPort = if (uri.scheme == "https") 443 else 80
            if (port != defaultPort) append(":$port")
        }
        return targetOrigin == current.origin
    }

    private fun isAllowedResource(uri: Uri): Boolean {
        when (uri.scheme) {
            "https" -> return true
            "http" -> return isPrivateOrLocalIpLiteral(uri.host)
            else -> return false
        }
    }

    fun openWebViewSettings() {
        runCatching {
            activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = "package:${activity.packageName}".toUri()
            })
        }
    }

    fun interface FileChooser {
        fun launch(
            callback: ValueCallback<Array<Uri>>,
            params: WebChromeClient.FileChooserParams,
        ): Boolean
    }

    private companion object {
        const val BRIDGE_OBJECT = "ccRemoteNative"
    }
}
