package dev.ccremote.pager.ui

import android.webkit.WebView
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import dev.ccremote.pager.PagerScreen
import dev.ccremote.pager.PagerUiState
import dev.ccremote.pager.domain.PagerTask

@Composable
fun PagerRoot(
    state: PagerUiState,
    webView: WebView,
    snackbarHostState: SnackbarHostState,
    onShowDashboard: () -> Unit,
    onShowChat: () -> Unit,
    onRefresh: () -> Unit,
    onOpenTask: (PagerTask) -> Unit,
    onInterrupt: (PagerTask) -> Unit,
    onAnswer: (PagerTask, String) -> Unit,
    onPin: (PagerTask, Boolean) -> Unit,
    onMarkRead: (PagerTask) -> Unit,
    onUpdateEndpoint: (String) -> Unit,
    onFeedbackEnabled: (Boolean) -> Unit,
) {
    val chatVisible = state.screen == PagerScreen.CHAT
    BackHandler(enabled = chatVisible && state.bridgeConnected) { onShowDashboard() }
    Box(Modifier.fillMaxSize()) {
        AndroidView(
            factory = { webView },
            modifier = Modifier.fillMaxSize().alpha(if (chatVisible) 1f else 0f),
            update = { view ->
                view.isEnabled = chatVisible
                view.isFocusable = chatVisible
                view.isFocusableInTouchMode = chatVisible
            },
        )
        if (!chatVisible) {
            PagerDashboard(
                state = state,
                onOpenChat = onShowChat,
                onRefresh = onRefresh,
                onOpenTask = onOpenTask,
                onInterrupt = onInterrupt,
                onAnswer = onAnswer,
                onPin = onPin,
                onMarkRead = onMarkRead,
                onUpdateEndpoint = onUpdateEndpoint,
                onFeedbackEnabled = onFeedbackEnabled,
            )
        } else if (state.bridgeConnected) {
            Button(
                onClick = onShowDashboard,
                modifier = Modifier.align(Alignment.TopStart).padding(12.dp),
            ) {
                Text("任务看板")
            }
        }
        SnackbarHost(
            hostState = snackbarHostState,
            modifier = Modifier.align(Alignment.BottomCenter),
        )
    }
}
