package dev.ccremote.pager.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import dev.ccremote.pager.PagerUiState
import dev.ccremote.pager.domain.PagerTask

@Composable
fun PagerDashboardRoot(
    state: PagerUiState,
    snackbarHostState: SnackbarHostState,
    onShowChat: () -> Unit,
    onRefresh: () -> Unit,
    onOpenTask: (PagerTask) -> Unit,
    onInterrupt: (PagerTask) -> Unit,
    onAnswer: (PagerTask, String) -> Unit,
    onPin: (PagerTask, Boolean) -> Unit,
    onMarkRead: (PagerTask) -> Unit,
    onUpdateEndpoint: (String) -> Unit,
    onScanPairing: () -> Unit,
    onFeedbackEnabled: (Boolean) -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
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
            onScanPairing = onScanPairing,
            onFeedbackEnabled = onFeedbackEnabled,
        )
        SnackbarHost(
            hostState = snackbarHostState,
            modifier = Modifier.align(Alignment.BottomCenter),
        )
    }
}
