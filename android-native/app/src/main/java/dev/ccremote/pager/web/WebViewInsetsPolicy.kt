package dev.ccremote.pager.web

/** Per-edge layout margins that keep the legacy WebView inside Android safe areas. */
internal data class WebViewInsets(
    val top: Int,
    val bottom: Int,
    val left: Int,
    val right: Int,
) {
    companion object {
        val ZERO = WebViewInsets(0, 0, 0, 0)
    }
}

/**
 * IME lift for the bottom edge: the full occluding inset while the keyboard is
 * visible, otherwise nothing. The insets-animation callback feeds intermediate
 * values through this while the keyboard animates.
 */
internal fun effectiveImeBottom(visible: Boolean, insetBottom: Int): Int =
    if (visible) insetBottom.coerceAtLeast(0) else 0

/**
 * Merge per-type system insets into the WebView's safe-area margins.
 *
 * [statusBarTop], [navBarLeft], [navBarRight], and [navBarBottom] come from
 * `WindowInsetsCompat.Type.systemBars()`; the cutout values come from
 * `WindowInsetsCompat.Type.displayCutout()`. Each edge takes the larger of its
 * sources so a notch, hole-punch, or rounded-corner display can never let the
 * page draw into a system area. The bottom edge is additionally lifted by the
 * IME while it is visible; otherwise it rests on the navigation-bar/cutout
 * safe inset. All values are clamped to zero so a malformed insets report
 * cannot push content off-screen.
 */
internal fun webViewInsets(
    statusBarTop: Int,
    navBarLeft: Int,
    navBarRight: Int,
    navBarBottom: Int,
    cutoutTop: Int,
    cutoutLeft: Int,
    cutoutRight: Int,
    cutoutBottom: Int,
    imeVisible: Boolean,
    imeBottom: Int,
): WebViewInsets {
    val safeTop = maxOf(statusBarTop, cutoutTop).coerceAtLeast(0)
    val safeBottom = maxOf(navBarBottom, cutoutBottom).coerceAtLeast(0)
    return WebViewInsets(
        top = safeTop,
        bottom = maxOf(effectiveImeBottom(imeVisible, imeBottom), safeBottom),
        left = maxOf(navBarLeft, cutoutLeft).coerceAtLeast(0),
        right = maxOf(navBarRight, cutoutRight).coerceAtLeast(0),
    )
}
