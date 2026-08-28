package dev.ccremote.pager

import dev.ccremote.pager.data.ServerEndpoint
import org.junit.Assert.assertEquals
import org.junit.Test

class PagerScreenTest {
    private val configured = ServerEndpoint.parse("https://remote.example.com").getOrThrow()

    @Test
    fun `first launch with no endpoint starts on the dashboard, never a blank webview`() {
        // Regression: the initial screen was hard-coded to CHAT, which hid the
        // dashboard (and its first-launch settings dialog) behind an
        // endpoint-less WebView on fresh installs — a permanently blank screen.
        assertEquals(PagerScreen.DASHBOARD, PagerScreen.initialFor(endpoint = null))
    }

    @Test
    fun `a configured endpoint still starts on chat so the webview loads and connects`() {
        assertEquals(PagerScreen.CHAT, PagerScreen.initialFor(endpoint = configured))
    }

    @Test
    fun `explicit navigation outranks the first-launch default`() {
        // updateEndpoint() explicitly switches to CHAT after saving so the
        // WebView can load/connect; that explicit navigation must not be
        // overridden by the no-endpoint dashboard default.
        assertEquals(
            PagerScreen.CHAT,
            resolveScreen(currentScreen = PagerScreen.CHAT, endpoint = null),
        )
    }
}
