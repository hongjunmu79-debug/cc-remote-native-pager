package dev.ccremote.pager.web

import android.view.ViewGroup
import android.webkit.WebView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsAnimationCompat
import androidx.core.view.WindowInsetsCompat

/** Keeps the legacy WebView viewport above the IME in edge-to-edge mode. */
class ImeInsetsController(
    private val webView: WebView,
) {
    private var appliedBottom = -1

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
        val bottom = effectiveImeBottom(
            visible = insets.isVisible(WindowInsetsCompat.Type.ime()),
            insetBottom = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom,
        )
        if (bottom == appliedBottom) return
        appliedBottom = bottom
        val params = webView.layoutParams as? ViewGroup.MarginLayoutParams ?: return
        if (params.bottomMargin == bottom) return
        params.bottomMargin = bottom
        webView.layoutParams = params
    }
}

internal fun effectiveImeBottom(visible: Boolean, insetBottom: Int): Int =
    if (visible) insetBottom.coerceAtLeast(0) else 0
