package dev.ccremote.pager.web

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebViewRevealPolicyTest {
    @Test
    fun chromium90RefreshesItsCompositorAfterReveal() {
        assertTrue(requiresCompositorRefreshAfterReveal("90.0.4430.210"))
    }

    @Test
    fun currentChromiumKeepsItsLiveSurface() {
        assertFalse(requiresCompositorRefreshAfterReveal("140.0.7339.51"))
    }

    @Test
    fun unknownVersionsDoNotTriggerDestructiveFallbacks() {
        assertFalse(requiresCompositorRefreshAfterReveal(null))
        assertFalse(requiresCompositorRefreshAfterReveal("invalid"))
    }
}
