package dev.ccremote.pager.ui

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import dev.ccremote.pager.domain.PagerActivity
import dev.ccremote.pager.domain.PagerLifecycle
import kotlin.math.PI
import kotlin.math.sin
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive

@Composable
fun PixelCore(
    lifecycle: PagerLifecycle,
    activity: PagerActivity?,
    modifier: Modifier = Modifier,
    motionEnabled: Boolean = true,
) {
    var phase by remember { mutableFloatStateOf(0f) }
    val animate = motionEnabled && when (lifecycle) {
        PagerLifecycle.RUNNING,
        PagerLifecycle.STARTING,
        PagerLifecycle.WAITING_ANSWER -> true
        else -> false
    }
    LaunchedEffect(animate, lifecycle, activity) {
        phase = 0f
        if (!animate) return@LaunchedEffect
        var previous = 0L
        while (currentCoroutineContext().isActive) {
            androidx.compose.runtime.withFrameNanos { frame ->
                if (previous == 0L || frame - previous >= FRAME_INTERVAL_NANOS) {
                    previous = frame
                    phase = (phase + 0.10f) % (2f * PI.toFloat())
                }
            }
        }
    }
    Canvas(modifier = modifier) {
        drawPixelCore(lifecycle, activity, phase)
    }
}

private fun DrawScope.drawPixelCore(
    lifecycle: PagerLifecycle,
    activity: PagerActivity?,
    phase: Float,
) {
    val color = lifecycleColor(lifecycle)
    val gap = size.minDimension * 0.055f
    val cell = (size.minDimension - gap * 2f) / 3f
    val startX = (size.width - (cell * 3f + gap * 2f)) / 2f
    val startY = (size.height - (cell * 3f + gap * 2f)) / 2f
    for (index in 0 until 9) {
        val row = index / 3
        val column = index % 3
        val wave = (sin(phase + index * 0.72f) + 1f) / 2f
        val activityBias = activityWeight(activity, row, column)
        val base = lifecycleIntensity(lifecycle)
        val intensity = (base + wave * animationRange(lifecycle) + activityBias)
            .coerceIn(0.08f, 1f)
        val left = startX + column * (cell + gap)
        val top = startY + row * (cell + gap)
        val center = Offset(left + cell / 2f, top + cell / 2f)
        if (intensity > 0.28f) {
            drawCircle(
                color = color.copy(alpha = intensity * 0.10f),
                radius = cell * 0.74f,
                center = center,
            )
        }
        drawRoundRect(
            color = color.copy(alpha = 0.18f + intensity * 0.82f),
            topLeft = Offset(left, top),
            size = Size(cell, cell),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(cell * 0.12f),
        )
    }
}

private fun lifecycleColor(value: PagerLifecycle): Color = when (value) {
    PagerLifecycle.OFFLINE -> PagerColors.Muted
    PagerLifecycle.IDLE -> PagerColors.Cyan
    PagerLifecycle.STARTING -> PagerColors.Purple
    PagerLifecycle.RUNNING -> PagerColors.Green
    PagerLifecycle.WAITING_ANSWER -> PagerColors.Yellow
    PagerLifecycle.SUCCEEDED -> PagerColors.Cyan
    PagerLifecycle.INTERRUPTED -> PagerColors.Red
}

private fun lifecycleIntensity(value: PagerLifecycle): Float = when (value) {
    PagerLifecycle.OFFLINE -> 0.10f
    PagerLifecycle.IDLE -> 0.22f
    PagerLifecycle.STARTING -> 0.30f
    PagerLifecycle.RUNNING -> 0.38f
    PagerLifecycle.WAITING_ANSWER -> 0.48f
    PagerLifecycle.SUCCEEDED -> 0.42f
    PagerLifecycle.INTERRUPTED -> 0.30f
}

private fun animationRange(value: PagerLifecycle): Float = when (value) {
    PagerLifecycle.RUNNING -> 0.52f
    PagerLifecycle.STARTING -> 0.38f
    PagerLifecycle.WAITING_ANSWER -> 0.30f
    else -> 0f
}

private fun activityWeight(activity: PagerActivity?, row: Int, column: Int): Float =
    when (activity) {
        PagerActivity.THINKING -> if (row == 1 && column == 1) 0.24f else 0f
        PagerActivity.READING -> if (column == 0) 0.18f else 0f
        PagerActivity.SEARCHING -> if (row == column) 0.20f else 0f
        PagerActivity.EDITING -> if (column == 2) 0.20f else 0f
        PagerActivity.EXECUTING -> if (row == 2) 0.20f else 0f
        PagerActivity.TESTING -> if ((row + column) % 2 == 0) 0.18f else 0f
        PagerActivity.BROWSING -> if (row == 0) 0.18f else 0f
        PagerActivity.DELEGATING -> if (row != 1 || column != 1) 0.14f else 0f
        null -> 0f
    }

private const val FRAME_INTERVAL_NANOS = 33_333_333L
