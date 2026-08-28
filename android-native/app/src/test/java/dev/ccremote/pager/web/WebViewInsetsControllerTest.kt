package dev.ccremote.pager.web

import org.junit.Assert.assertEquals
import org.junit.Test

class WebViewInsetsControllerTest {
    @Test
    fun visibleImeUsesTheFullOccludedInset() {
        assertEquals(812, effectiveImeBottom(visible = true, insetBottom = 812))
    }

    @Test
    fun hiddenImeClearsTheImeLift() {
        assertEquals(0, effectiveImeBottom(visible = false, insetBottom = 812))
    }

    @Test
    fun malformedNegativeInsetsAreClamped() {
        assertEquals(0, effectiveImeBottom(visible = true, insetBottom = -1))
    }

    @Test
    fun statusBarInsetOffsetsTheTopEdge() {
        val insets = webViewInsets(
            statusBarTop = 63,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 48,
            cutoutTop = 0,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = false,
            imeBottom = 0,
        )
        assertEquals(WebViewInsets(top = 63, bottom = 48, left = 0, right = 0), insets)
    }

    @Test
    fun displayCutoutDeeperThanStatusBarWinsAtTheTop() {
        val insets = webViewInsets(
            statusBarTop = 24,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 0,
            cutoutTop = 56,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = false,
            imeBottom = 0,
        )
        assertEquals(56, insets.top)
    }

    @Test
    fun landscapeSideCutoutsInsetLeftAndRight() {
        val insets = webViewInsets(
            statusBarTop = 24,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 48,
            cutoutTop = 24,
            cutoutLeft = 60,
            cutoutRight = 60,
            cutoutBottom = 0,
            imeVisible = false,
            imeBottom = 0,
        )
        assertEquals(WebViewInsets(top = 24, bottom = 48, left = 60, right = 60), insets)
    }

    @Test
    fun hiddenImeRestsOnNavigationBarSafeInset() {
        val insets = webViewInsets(
            statusBarTop = 24,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 56,
            cutoutTop = 0,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = false,
            imeBottom = 900,
        )
        assertEquals(56, insets.bottom)
    }

    @Test
    fun visibleImeLiftsAboveTheNavigationBarInset() {
        val insets = webViewInsets(
            statusBarTop = 24,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 48,
            cutoutTop = 0,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = true,
            imeBottom = 812,
        )
        assertEquals(812, insets.bottom)
    }

    @Test
    fun visibleImeSmallerThanNavigationBarStillKeepsTheSafeBottom() {
        val insets = webViewInsets(
            statusBarTop = 24,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 120,
            cutoutTop = 0,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = true,
            imeBottom = 48,
        )
        assertEquals(120, insets.bottom)
    }

    @Test
    fun noInsetsProduceZeroMargins() {
        val insets = webViewInsets(
            statusBarTop = 0,
            navBarLeft = 0,
            navBarRight = 0,
            navBarBottom = 0,
            cutoutTop = 0,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = false,
            imeBottom = 0,
        )
        assertEquals(WebViewInsets.ZERO, insets)
    }

    @Test
    fun malformedNegativeSystemInsetsAreClampedToZero() {
        val insets = webViewInsets(
            statusBarTop = -5,
            navBarLeft = -1,
            navBarRight = -1,
            navBarBottom = -3,
            cutoutTop = 0,
            cutoutLeft = 0,
            cutoutRight = 0,
            cutoutBottom = 0,
            imeVisible = false,
            imeBottom = 0,
        )
        assertEquals(WebViewInsets.ZERO, insets)
    }
}
