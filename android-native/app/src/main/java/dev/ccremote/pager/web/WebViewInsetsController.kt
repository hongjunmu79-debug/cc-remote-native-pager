package dev.ccremote.pager.web

import android.view.ViewGroup
import android.webkit.WebView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsAnimationCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Sole owner of the legacy WebView's window-inset handling.
 *
 * The legacy CHAT surface is a full-bleed WebView under `enableEdgeToEdge()`,
 * so without intervention its page draws beneath the status bar, navigation
 * bar, and display cutout. This controller keeps the WebView content inside
 * the Android safe insets by applying them as layout margins, and lifts the
 * bottom edge above the IME while the keyboard is visible. It installs the
 * only `OnApplyWindowInsetsListener` on the WebView, so no second listener can
 * compete for the same insets dispatch.
 */
class WebViewInsetsController(
    private val webView: WebView,
) {
    private var applied: WebViewInsets? = null

    fun install() {
        ViewCompat.setOnApplyWindowInsetsListener(webView) { _, insets ->
            applyInsets(insets)
            insets
        }
        ViewCompat.setWindowInsetsAnimationCallback(
            webView,
            object : WindowInsetsAnimationCompat.Callback(
                WindowInsetsAnimationCompat.Callback.DISPATCH_MODE_CONTINUE_ON_SUBTREE,
            ) {
                override fun onProgress(
                    insets: WindowInsetsCompat,
                    runningAnimations: MutableList<WindowInsetsAnimationCompat>,
                ): WindowInsetsCompat {
                    applyInsets(insets)
                    return insets
                }
            },
        )
        ViewCompat.requestApplyInsets(webView)
    }

    fun clear() {
        ViewCompat.setOnApplyWindowInsetsListener(webView, null)
        ViewCompat.setWindowInsetsAnimationCallback(webView, null)
    }

    private fun applyInsets(insets: WindowInsetsCompat) {
        val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
        val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
        val target = webViewInsets(
            statusBarTop = systemBars.top,
            navBarLeft = systemBars.left,
            navBarRight = systemBars.right,
            navBarBottom = systemBars.bottom,
            cutoutTop = cutout.top,
            cutoutLeft = cutout.left,
            cutoutRight = cutout.right,
            cutoutBottom = cutout.bottom,
            imeVisible = insets.isVisible(WindowInsetsCompat.Type.ime()),
            imeBottom = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom,
        )
        if (target == applied) return
        applied = target
        val params = webView.layoutParams as? ViewGroup.MarginLayoutParams ?: return
        if (params.topMargin != target.top ||
            params.bottomMargin != target.bottom ||
            params.leftMargin != target.left ||
            params.rightMargin != target.right
        ) {
            params.setMargins(target.left, target.top, target.right, target.bottom)
            webView.layoutParams = params
        }
    }
}
