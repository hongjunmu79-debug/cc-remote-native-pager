package dev.ccremote.pager.web

import org.junit.Assert.assertEquals
import org.junit.Test

class ImeInsetsControllerTest {
    @Test
    fun visibleImeUsesTheFullOccludedInset() {
        assertEquals(812, effectiveImeBottom(visible = true, insetBottom = 812))
    }

    @Test
    fun hiddenImeClearsTheViewportMargin() {
        assertEquals(0, effectiveImeBottom(visible = false, insetBottom = 812))
    }

    @Test
    fun malformedNegativeInsetsAreClamped() {
        assertEquals(0, effectiveImeBottom(visible = true, insetBottom = -1))
    }
}
