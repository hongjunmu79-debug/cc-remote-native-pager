package dev.ccremote.pager.web

private const val LAST_STALE_REVEAL_WEBVIEW_MAJOR = 90

/**
 * Affected vendor Chromium builds keep their pre-auth frame after a hardware
 * WebView is detached and reattached. Reloading only on reveal preserves the
 * authenticated cookie and browser cache while forcing a fresh compositor.
 */
internal fun requiresReloadAfterReveal(versionName: String?): Boolean {
    val major = versionName
        ?.substringBefore('.')
        ?.toIntOrNull()
        ?: return false
    return major <= LAST_STALE_REVEAL_WEBVIEW_MAJOR
}
