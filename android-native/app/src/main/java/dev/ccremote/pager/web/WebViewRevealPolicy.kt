package dev.ccremote.pager.web

private const val LAST_STALE_REVEAL_WEBVIEW_MAJOR = 90

/**
 * Affected vendor Chromium builds can retain an old hardware-compositor frame
 * while a native overlay is visible. Refresh their layer on reveal, but keep the
 * live document and WebSocket whenever its JavaScript bridge still responds.
 */
internal fun requiresCompositorRefreshAfterReveal(versionName: String?): Boolean {
    val major = versionName
        ?.substringBefore('.')
        ?.toIntOrNull()
        ?: return false
    return major <= LAST_STALE_REVEAL_WEBVIEW_MAJOR
}
