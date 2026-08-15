package dev.ccremote.pager

import android.app.Application
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.ccremote.pager.bridge.NativeBridgeRepository
import dev.ccremote.pager.bridge.NativeCommandAck
import dev.ccremote.pager.bridge.PagerCommand
import dev.ccremote.pager.bridge.PagerCommandAction
import dev.ccremote.pager.data.AppPreferences
import dev.ccremote.pager.data.ServerEndpoint
import dev.ccremote.pager.domain.PagerLifecycle
import dev.ccremote.pager.domain.PagerTask
import java.util.UUID
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class PagerScreen {
    CHAT,
    DASHBOARD,
}

data class PagerUiState(
    val screen: PagerScreen = PagerScreen.CHAT,
    val tasks: List<PagerTask> = emptyList(),
    val focusedTaskId: String? = null,
    val bridgeConnected: Boolean = false,
    val wrapperOnline: Boolean = false,
    val machineId: String = "",
    val endpoint: ServerEndpoint = ServerEndpoint.Default,
    val feedbackEnabled: Boolean = true,
)

sealed interface PagerUiEvent {
    data class Message(val value: String) : PagerUiEvent
    data class Feedback(val task: PagerTask) : PagerUiEvent
}

class PagerViewModel(application: Application) : AndroidViewModel(application) {
    val bridge = NativeBridgeRepository(elapsedRealtime = SystemClock::elapsedRealtime)
    private val preferences = AppPreferences(application.applicationContext)
    private val screen = MutableStateFlow(PagerScreen.CHAT)
    private var automaticDashboardShown = false
    private val pendingCommands = linkedMapOf<String, PendingCommand>()
    private var pendingOpenTaskId: String? = null

    private val mutableCommands = MutableSharedFlow<String>(extraBufferCapacity = 32)
    val commands = mutableCommands.asSharedFlow()

    private val mutableEvents = MutableSharedFlow<PagerUiEvent>(extraBufferCapacity = 32)
    val events = mutableEvents.asSharedFlow()

    private val freshnessClock = flow {
        while (true) {
            emit(SystemClock.elapsedRealtime())
            delay(5_000)
        }
    }

    val uiState: StateFlow<PagerUiState> = combine(
        bridge.state,
        preferences.values,
        screen,
        freshnessClock,
    ) { bridgeState, preferenceState, currentScreen, now ->
        val snapshot = bridgeState.snapshot
        val tasks = snapshot?.tasks.orEmpty().map { task ->
            val revision = task.completedRevision
            task.copy(unread = revision != null
                && preferenceState.seenRevisions[task.id] != revision)
        }.sortedWith(TASK_COMPARATOR)
        PagerUiState(
            screen = currentScreen,
            tasks = tasks,
            focusedTaskId = snapshot?.focusedTaskId,
            bridgeConnected = bridgeState.lastFrameAtElapsed?.let {
                now - it <= BRIDGE_STALE_MILLIS
            } ?: false,
            wrapperOnline = snapshot?.wrapperOnline == true,
            machineId = snapshot?.machineId.orEmpty(),
            endpoint = preferenceState.endpoint,
            feedbackEnabled = preferenceState.feedbackEnabled,
        )
    }.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        PagerUiState(),
    )

    init {
        viewModelScope.launch {
            var previous = emptyMap<String, PagerLifecycle>()
            bridge.state.collect { state ->
                val snapshot = state.snapshot ?: return@collect
                if (!automaticDashboardShown) {
                    automaticDashboardShown = true
                    screen.value = PagerScreen.DASHBOARD
                }
                pendingOpenTaskId?.let { taskId ->
                    snapshot.tasks.firstOrNull { it.id == taskId }?.let { task ->
                        pendingOpenTaskId = null
                        openTask(task)
                    }
                }
                val current = snapshot.tasks.associate { it.id to it.lifecycle }
                snapshot.tasks.forEach { task ->
                    val before = previous[task.id]
                    if (before != null && before != task.lifecycle
                        && task.lifecycle in ATTENTION_LIFECYCLES
                    ) {
                        mutableEvents.tryEmit(PagerUiEvent.Feedback(task))
                    }
                }
                previous = current
            }
        }
        viewModelScope.launch {
            bridge.acknowledgements.collect(::handleAck)
        }
    }

    fun showDashboard() {
        screen.value = PagerScreen.DASHBOARD
    }

    fun showChat() {
        screen.value = PagerScreen.CHAT
    }

    fun openTaskWhenAvailable(taskId: String) {
        val task = uiState.value.tasks.firstOrNull { it.id == taskId }
        if (task != null) openTask(task) else pendingOpenTaskId = taskId
    }

    fun openTask(task: PagerTask) {
        send(PagerCommandAction.FocusTask(task.id))
        markRead(task)
        screen.value = PagerScreen.CHAT
    }

    fun interrupt(task: PagerTask) = send(PagerCommandAction.InterruptTask(task.id))

    fun answer(task: PagerTask, answer: String) {
        if (answer.isBlank()) {
            mutableEvents.tryEmit(PagerUiEvent.Message("回答不能为空"))
            return
        }
        send(PagerCommandAction.AnswerQuestion(task.id, answer))
    }

    fun setPinned(task: PagerTask, pinned: Boolean) =
        send(PagerCommandAction.SetPinned(task.id, pinned))

    fun refresh() = send(PagerCommandAction.RefreshSessions)

    fun markRead(task: PagerTask) {
        val revision = task.completedRevision ?: return
        viewModelScope.launch { preferences.markSeen(task.id, revision) }
    }

    fun updateEndpoint(value: String) {
        viewModelScope.launch {
            preferences.setServerUrl(value)
                .onSuccess {
                    bridge.reset()
                    pendingCommands.clear()
                    automaticDashboardShown = false
                    screen.value = PagerScreen.CHAT
                }
                .onFailure { error ->
                    mutableEvents.emit(
                        PagerUiEvent.Message(error.message ?: "服务器地址无效"),
                    )
                }
        }
    }

    fun reportPageProblem(message: String?) {
        if (message.isNullOrBlank()) return
        mutableEvents.tryEmit(PagerUiEvent.Message(message))
    }

    fun setFeedbackEnabled(enabled: Boolean) {
        viewModelScope.launch { preferences.setFeedbackEnabled(enabled) }
    }

    private fun send(action: PagerCommandAction) {
        val now = SystemClock.elapsedRealtime()
        pendingCommands.entries.removeAll { now - it.value.createdAt > COMMAND_TTL_MILLIS }
        if (pendingCommands.size >= MAX_PENDING_COMMANDS) {
            mutableEvents.tryEmit(PagerUiEvent.Message("仍有过多操作等待网页端确认"))
            return
        }
        val command = PagerCommand(UUID.randomUUID().toString(), action)
        bridge.encode(command).onSuccess { raw ->
            pendingCommands[command.commandId] = PendingCommand(now)
            if (!mutableCommands.tryEmit(raw)) {
                pendingCommands.remove(command.commandId)
                mutableEvents.tryEmit(PagerUiEvent.Message("命令队列暂时不可用"))
            }
        }.onFailure { error ->
            mutableEvents.tryEmit(
                PagerUiEvent.Message(error.message ?: "无法创建命令"),
            )
        }
    }

    private fun handleAck(ack: NativeCommandAck) {
        if (pendingCommands.remove(ack.commandId) == null) return
        if (!ack.accepted) {
            mutableEvents.tryEmit(
                PagerUiEvent.Message(ack.message ?: "操作未被网页端接受"),
            )
        }
    }

    private companion object {
        const val BRIDGE_STALE_MILLIS = 45_000L
        const val COMMAND_TTL_MILLIS = 60_000L
        const val MAX_PENDING_COMMANDS = 64
        val ATTENTION_LIFECYCLES = setOf(
            PagerLifecycle.WAITING_ANSWER,
            PagerLifecycle.SUCCEEDED,
            PagerLifecycle.INTERRUPTED,
        )
        val TASK_COMPARATOR = compareByDescending<PagerTask> {
            when (it.lifecycle) {
                PagerLifecycle.WAITING_ANSWER -> 5
                PagerLifecycle.RUNNING, PagerLifecycle.STARTING -> 4
                PagerLifecycle.SUCCEEDED -> if (it.unread) 3 else 1
                PagerLifecycle.INTERRUPTED -> 2
                PagerLifecycle.IDLE, PagerLifecycle.OFFLINE -> 0
            }
        }.thenByDescending { it.pinned }
            .thenByDescending { it.updatedAt }
            .thenBy { it.id }
    }

    private data class PendingCommand(
        val createdAt: Long,
    )
}
