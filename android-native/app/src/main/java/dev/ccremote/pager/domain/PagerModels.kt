package dev.ccremote.pager.domain

enum class PagerLifecycle {
    OFFLINE,
    IDLE,
    STARTING,
    RUNNING,
    WAITING_ANSWER,
    SUCCEEDED,
    INTERRUPTED,
}

enum class PagerActivity {
    THINKING,
    READING,
    SEARCHING,
    EDITING,
    EXECUTING,
    TESTING,
    BROWSING,
    DELEGATING,
}

enum class PagerCapability {
    OPEN_CHAT,
    INTERRUPT,
    ANSWER,
    PIN,
}

data class PagerSubagent(
    val id: String,
    val title: String,
    val state: PagerSubagentState,
    val latestStep: String? = null,
)

enum class PagerSubagentState {
    RUNNING,
    SUCCEEDED,
    FAILED,
    INTERRUPTED,
}

data class PagerQuestion(
    val header: String? = null,
    val question: String,
    val options: List<String>,
    val allowText: Boolean,
    val secret: Boolean,
)

data class PagerTask(
    val id: String,
    val engine: PagerEngine,
    val projectName: String,
    val title: String,
    val lifecycle: PagerLifecycle,
    val activity: PagerActivity? = null,
    val latestStep: String? = null,
    val startedAt: Long,
    val updatedAt: Long,
    val completedAt: Long? = null,
    val completedRevision: String? = null,
    val pinned: Boolean,
    val focused: Boolean,
    val capabilities: Set<PagerCapability>,
    val subagents: List<PagerSubagent>,
    val question: PagerQuestion? = null,
    val unread: Boolean = false,
)

enum class PagerEngine {
    CLAUDE,
    CODEX,
}

data class PagerSnapshot(
    val bridgeInstanceId: String,
    val sequence: Long,
    val emittedAt: Long,
    val connection: PagerConnection,
    val wrapperOnline: Boolean,
    val machineId: String,
    val focusedTaskId: String?,
    val tasks: List<PagerTask>,
)

enum class PagerConnection {
    CONNECTING,
    CONNECTED,
    RECONNECTING,
    DISCONNECTED,
}
