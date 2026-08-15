package dev.ccremote.pager

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.ccremote.pager.feedback.PagerFeedbackController
import dev.ccremote.pager.ui.CCRemotePagerTheme
import dev.ccremote.pager.ui.PagerRoot
import dev.ccremote.pager.web.SecureWebViewController

class MainActivity : ComponentActivity() {
    private val viewModel by viewModels<PagerViewModel>()
    private lateinit var webController: SecureWebViewController
    private lateinit var feedbackController: PagerFeedbackController
    private var pendingFileCallback: ValueCallback<Array<Uri>>? = null
    private var notificationPermissionRequested = false

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val callback = pendingFileCallback ?: return@registerForActivityResult
        pendingFileCallback = null
        callback.onReceiveValue(
            WebChromeClient.FileChooserParams.parseResult(
                result.resultCode,
                result.data,
            ),
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        feedbackController = PagerFeedbackController(applicationContext)
        webController = SecureWebViewController(
            activity = this,
            initialEndpoint = viewModel.uiState.value.endpoint,
            onBridgeMessage = viewModel.bridge::accept,
            onPageProblem = viewModel::reportPageProblem,
            fileChooser = SecureWebViewController.FileChooser(::launchFileChooser),
        )
        intent.getStringExtra(EXTRA_OPEN_TASK_ID)?.let(viewModel::openTaskWhenAvailable)

        setContent {
            val state by viewModel.uiState.collectAsStateWithLifecycle()
            val snackbar = remember { SnackbarHostState() }

            LaunchedEffect(state.bridgeConnected, state.feedbackEnabled) {
                if (state.bridgeConnected && state.feedbackEnabled) {
                    ensureNotificationPermission()
                }
            }

            LaunchedEffect(Unit) {
                viewModel.commands.collect(webController::sendCommand)
            }
            LaunchedEffect(state.endpoint) {
                webController.reconfigure(state.endpoint)
            }
            LaunchedEffect(Unit) {
                viewModel.events.collect { event ->
                    when (event) {
                        is PagerUiEvent.Message -> snackbar.showSnackbar(event.value)
                        is PagerUiEvent.Feedback -> {
                            if (viewModel.uiState.value.feedbackEnabled) {
                                feedbackController.notify(event.task)
                            }
                        }
                    }
                }
            }

            CCRemotePagerTheme {
                PagerRoot(
                    state = state,
                    webView = webController.webView,
                    snackbarHostState = snackbar,
                    onShowDashboard = viewModel::showDashboard,
                    onShowChat = viewModel::showChat,
                    onRefresh = viewModel::refresh,
                    onOpenTask = viewModel::openTask,
                    onInterrupt = viewModel::interrupt,
                    onAnswer = viewModel::answer,
                    onPin = viewModel::setPinned,
                    onMarkRead = viewModel::markRead,
                    onUpdateEndpoint = viewModel::updateEndpoint,
                    onFeedbackEnabled = { enabled ->
                        viewModel.setFeedbackEnabled(enabled)
                        if (enabled) ensureNotificationPermission()
                    },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra(EXTRA_OPEN_TASK_ID)?.let(viewModel::openTaskWhenAvailable)
    }

    override fun onResume() {
        super.onResume()
        if (::webController.isInitialized) webController.onResume()
    }

    override fun onPause() {
        if (::webController.isInitialized) webController.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        pendingFileCallback?.onReceiveValue(null)
        pendingFileCallback = null
        if (::webController.isInitialized) webController.destroy()
        if (::feedbackController.isInitialized) feedbackController.close()
        super.onDestroy()
    }

    private fun launchFileChooser(
        callback: ValueCallback<Array<Uri>>,
        params: WebChromeClient.FileChooserParams,
    ): Boolean {
        pendingFileCallback?.onReceiveValue(null)
        pendingFileCallback = callback
        return runCatching {
            fileChooserLauncher.launch(params.createIntent())
            true
        }.getOrElse {
            pendingFileCallback = null
            callback.onReceiveValue(null)
            false
        }
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33 || notificationPermissionRequested) return
        if (ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
        ) return
        notificationPermissionRequested = true
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    companion object {
        const val EXTRA_OPEN_TASK_ID = "dev.ccremote.pager.OPEN_TASK_ID"
    }
}
