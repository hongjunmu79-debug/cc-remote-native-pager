package dev.ccremote.pager.bridge

import dev.ccremote.pager.domain.PagerActivity
import dev.ccremote.pager.domain.PagerCapability
import dev.ccremote.pager.domain.PagerConnection
import dev.ccremote.pager.domain.PagerEngine
import dev.ccremote.pager.domain.PagerLifecycle
import dev.ccremote.pager.domain.PagerQuestion
import dev.ccremote.pager.domain.PagerSnapshot
import dev.ccremote.pager.domain.PagerSubagent
import dev.ccremote.pager.domain.PagerSubagentState
import dev.ccremote.pager.domain.PagerTask
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

internal const val BRIDGE_VERSION = 1
internal const val MAX_BRIDGE_FRAME_BYTES = 256 * 1024
internal const val MAX_COMMAND_FRAME_BYTES = 16 * 1024
internal const val MAX_TASKS = 64
internal const val MAX_SUBAGENTS = 16

@Serializable
internal data class SnapshotEnvelopeDto(
    val bridgeVersion: Int,
    val type: String,
    val bridgeInstanceId: String,
    val sequence: Long,
    val emittedAt: Long,
    val payload: SnapshotPayloadDto,
)

@Serializable
internal data class SnapshotPayloadDto(
    val auth: String,
    val connection: String,
    val wrapperOnline: Boolean,
    val machineId: String,
    val focusedTaskId: String? = null,
    val tasks: List<TaskDto>,
)

@Serializable
internal data class TaskDto(
    val id: String,
    val engine: String,
    val projectName: String,
    val title: String,
    val lifecycle: String,
    val activity: String? = null,
    val latestStep: String? = null,
    val startedAt: Long,
    val updatedAt: Long,
    val completedAt: Long? = null,
    val completedRevision: String? = null,
    val pinned: Boolean,
    val focused: Boolean,
    val capabilities: List<String>,
    val subagents: List<SubagentDto>,
    val question: QuestionDto? = null,
)

@Serializable
internal data class SubagentDto(
    val id: String,
    val title: String,
    val state: String,
    val latestStep: String? = null,
)

@Serializable
internal data class QuestionDto(
    val header: String? = null,
    val question: String,
    val options: List<String>,
    val allowText: Boolean,
    val secret: Boolean,
)

@Serializable
internal data class HeartbeatEnvelopeDto(
    val bridgeVersion: Int,
    val type: String,
    val bridgeInstanceId: String,
    val emittedAt: Long,
)

@Serializable
internal data class CommandAckEnvelopeDto(
    val bridgeVersion: Int,
    val type: String,
    val bridgeInstanceId: String,
    val emittedAt: Long,
    val commandId: String,
    val accepted: Boolean,
    val message: String? = null,
)

internal sealed interface BridgeInboundEvent {
    data class Snapshot(val value: PagerSnapshot) : BridgeInboundEvent
    data class Heartbeat(val bridgeInstanceId: String, val emittedAt: Long) : BridgeInboundEvent
    data class CommandAck(
        val bridgeInstanceId: String,
        val commandId: String,
        val accepted: Boolean,
        val message: String?,
    ) : BridgeInboundEvent
}

sealed interface PagerCommandAction {
    data class FocusTask(val taskId: String) : PagerCommandAction
    data class InterruptTask(val taskId: String) : PagerCommandAction
    data class AnswerQuestion(val taskId: String, val answer: String) : PagerCommandAction
    data class SetPinned(val taskId: String, val pinned: Boolean) : PagerCommandAction
    data object RefreshSessions : PagerCommandAction
}

data class PagerCommand(
    val commandId: String,
    val action: PagerCommandAction,
)

internal fun SnapshotEnvelopeDto.toDomain(): PagerSnapshot {
    require(bridgeVersion == BRIDGE_VERSION && type == "snapshot")
    require(sequence > 0 && emittedAt > 0)
    require(payload.auth == "authenticated")
    require(payload.machineId.length in 1..128)
    require(payload.tasks.size <= MAX_TASKS)
    return PagerSnapshot(
        bridgeInstanceId = bridgeInstanceId.validatedBridgeInstanceId(),
        sequence = sequence,
        emittedAt = emittedAt,
        connection = payload.connection.toConnection(),
        wrapperOnline = payload.wrapperOnline,
        machineId = payload.machineId,
        focusedTaskId = payload.focusedTaskId?.validatedId(),
        tasks = payload.tasks.map(TaskDto::toDomain),
    )
}

private fun TaskDto.toDomain(): PagerTask {
    require(projectName.length in 1..80)
    require(title.length in 1..160)
    require(latestStep == null || latestStep.length <= 240)
    require(startedAt >= 0 && updatedAt >= 0)
    require(completedAt == null || completedAt >= 0)
    require(completedRevision == null || completedRevision.length <= 512)
    require(subagents.size <= MAX_SUBAGENTS)
    return PagerTask(
        id = id.validatedId(),
        engine = when (engine) {
            "claude" -> PagerEngine.CLAUDE
            "codex" -> PagerEngine.CODEX
            else -> error("Unsupported engine")
        },
        projectName = projectName,
        title = title,
        lifecycle = lifecycle.toLifecycle(),
        activity = activity?.toActivity(),
        latestStep = latestStep,
        startedAt = startedAt,
        updatedAt = updatedAt,
        completedAt = completedAt,
        completedRevision = completedRevision,
        pinned = pinned,
        focused = focused,
        capabilities = capabilities.mapTo(linkedSetOf(), String::toCapability),
        subagents = subagents.map(SubagentDto::toDomain),
        question = question?.toDomain(),
    )
}

private fun SubagentDto.toDomain(): PagerSubagent {
    require(title.length in 1..120)
    require(latestStep == null || latestStep.length <= 160)
    return PagerSubagent(
        id = id.validatedId(),
        title = title,
        state = when (state) {
            "running" -> PagerSubagentState.RUNNING
            "succeeded" -> PagerSubagentState.SUCCEEDED
            "failed" -> PagerSubagentState.FAILED
            "interrupted" -> PagerSubagentState.INTERRUPTED
            else -> error("Unsupported subagent state")
        },
        latestStep = latestStep,
    )
}

private fun QuestionDto.toDomain(): PagerQuestion {
    require(header == null || header.length <= 80)
    require(question.length in 1..512)
    require(options.size <= 12 && options.all { it.length in 1..120 })
    return PagerQuestion(header, question, options, allowText, secret)
}

private fun String.validatedId(): String {
    require(length in 1..256 && ID_PATTERN.matches(this))
    return this
}

private fun String.validatedBridgeInstanceId(): String {
    require(length in 8..64 && BRIDGE_INSTANCE_ID_PATTERN.matches(this))
    return this
}

private fun String.toConnection(): PagerConnection = when (this) {
    "connecting" -> PagerConnection.CONNECTING
    "connected" -> PagerConnection.CONNECTED
    "reconnecting" -> PagerConnection.RECONNECTING
    "disconnected" -> PagerConnection.DISCONNECTED
    else -> error("Unsupported connection")
}

private fun String.toLifecycle(): PagerLifecycle = when (this) {
    "offline" -> PagerLifecycle.OFFLINE
    "idle" -> PagerLifecycle.IDLE
    "starting" -> PagerLifecycle.STARTING
    "running" -> PagerLifecycle.RUNNING
    "waitingAnswer" -> PagerLifecycle.WAITING_ANSWER
    "succeeded" -> PagerLifecycle.SUCCEEDED
    "interrupted" -> PagerLifecycle.INTERRUPTED
    else -> error("Unsupported lifecycle")
}

private fun String.toActivity(): PagerActivity = when (this) {
    "thinking" -> PagerActivity.THINKING
    "reading" -> PagerActivity.READING
    "searching" -> PagerActivity.SEARCHING
    "editing" -> PagerActivity.EDITING
    "executing" -> PagerActivity.EXECUTING
    "testing" -> PagerActivity.TESTING
    "browsing" -> PagerActivity.BROWSING
    "delegating" -> PagerActivity.DELEGATING
    else -> error("Unsupported activity")
}

private fun String.toCapability(): PagerCapability = when (this) {
    "openChat" -> PagerCapability.OPEN_CHAT
    "interrupt" -> PagerCapability.INTERRUPT
    "answer" -> PagerCapability.ANSWER
    "pin" -> PagerCapability.PIN
    else -> error("Unsupported capability")
}

private val ID_PATTERN = Regex("^[A-Za-z0-9][A-Za-z0-9._:@/\\-]{0,255}$")
private val BRIDGE_INSTANCE_ID_PATTERN = Regex("^[A-Za-z0-9-]{8,64}$")
