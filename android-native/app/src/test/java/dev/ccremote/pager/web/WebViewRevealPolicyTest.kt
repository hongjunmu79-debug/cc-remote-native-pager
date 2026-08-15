package dev.ccremote.pager.web

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebViewRevealPolicyTest {
    @Test
    fun chromium90ReloadsAfterReveal() {
        assertTrue(requiresReloadAfterReveal("90.0.4430.210"))
    }

    @Test
    fun currentChromiumKeepsItsLiveSurface() {
        assertFalse(requiresReloadAfterReveal("140.0.7339.51"))
    }

    @Test
    fun unknownVersionsDoNotTriggerDestructiveFallbacks() {
        assertFalse(requiresReloadAfterReveal(null))
        assertFalse(requiresReloadAfterReveal("invalid"))
    }
}
