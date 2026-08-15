package dev.ccremote.pager.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object PagerColors {
    val Background = Color(0xFF090D12)
    val Surface = Color(0xFF101820)
    val SurfaceRaised = Color(0xFF17222C)
    val Grid = Color(0xFF263746)
    val Text = Color(0xFFF1F6F8)
    val Muted = Color(0xFF93A5B1)
    val Green = Color(0xFF5EEA9D)
    val Cyan = Color(0xFF54D8FF)
    val Yellow = Color(0xFFFFD166)
    val Red = Color(0xFFFF6B6B)
    val Purple = Color(0xFFB29BFF)
}
private val DarkColors = darkColorScheme(
    primary = PagerColors.Cyan,
    onPrimary = Color(0xFF001C26),
    secondary = PagerColors.Green,
    tertiary = PagerColors.Purple,
    background = PagerColors.Background,
    onBackground = PagerColors.Text,
    surface = PagerColors.Surface,
    onSurface = PagerColors.Text,
    surfaceVariant = PagerColors.SurfaceRaised,
    onSurfaceVariant = PagerColors.Muted,
    error = PagerColors.Red,
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF006685),
    secondary = Color(0xFF006C49),
    background = Color(0xFFF4F7F8),
    surface = Color.White,
    surfaceVariant = Color(0xFFE5EDF1),
    error = Color(0xFFBA1A1A),
)

@Composable
fun CCRemotePagerTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content,
    )
}
