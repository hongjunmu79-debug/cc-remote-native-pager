package dev.ccremote.pager.bridge

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.builtins.serializer

internal class BridgeParser(
    private val json: Json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        isLenient = false
    },
) {
    fun parse(raw: String): Result<BridgeInboundEvent> = runCatching {
        require(raw.toByteArray(Charsets.UTF_8).size <= MAX_BRIDGE_FRAME_BYTES) {
            "Bridge frame exceeds size limit"
        }
        val root = json.parseToJsonElement(raw) as? JsonObject
            ?: error("Bridge frame must be an object")
        val version = root["bridgeVersion"]?.jsonPrimitive?.content?.toIntOrNull()
        require(version == BRIDGE_VERSION) { "Unsupported bridge version" }
        when (root["type"]?.jsonPrimitive?.content) {
            "snapshot" -> BridgeInboundEvent.Snapshot(
                json.decodeFromJsonElement(SnapshotEnvelopeDto.serializer(), root).toDomain(),
            )
            "heartbeat" -> {
                val value = json.decodeFromJsonElement(HeartbeatEnvelopeDto.serializer(), root)
                require(value.emittedAt > 0 && BRIDGE_INSTANCE_ID_PATTERN.matches(value.bridgeInstanceId))
                BridgeInboundEvent.Heartbeat(value.bridgeInstanceId, value.emittedAt)
            }
            "commandAck" -> {
                val value = json.decodeFromJsonElement(CommandAckEnvelopeDto.serializer(), root)
                require(COMMAND_ID_PATTERN.matches(value.commandId))
                require(BRIDGE_INSTANCE_ID_PATTERN.matches(value.bridgeInstanceId))
                require(value.message == null || value.message.length <= 240)
                BridgeInboundEvent.CommandAck(
                    value.bridgeInstanceId,
                    value.commandId,
                    value.accepted,
                    value.message,
                )
            }
            else -> error("Unsupported bridge frame type")
        }
    }

    fun encodeCommand(command: PagerCommand): String {
        require(COMMAND_ID_PATTERN.matches(command.commandId))
        val action = when (val value = command.action) {
            is PagerCommandAction.FocusTask -> actionJson(
                "focusTask", "taskId" to value.taskId.validCommandTaskId(),
            )
            is PagerCommandAction.InterruptTask -> actionJson(
                "interruptTask", "taskId" to value.taskId.validCommandTaskId(),
            )
            is PagerCommandAction.AnswerQuestion -> {
                require(value.answer.toByteArray(Charsets.UTF_8).size <= 8 * 1024)
                actionJson(
                    "answerQuestion",
                    "taskId" to value.taskId.validCommandTaskId(),
                    "answer" to value.answer,
                )
            }
            is PagerCommandAction.SetPinned -> buildString {
                append("{\"kind\":\"setPinned\",\"taskId\":")
                append(json.encodeToString(String.serializer(), value.taskId.validCommandTaskId()))
                append(",\"pinned\":")
                append(value.pinned)
                append('}')
            }
            PagerCommandAction.RefreshSessions -> "{\"kind\":\"refreshSessions\"}"
        }
        val raw = buildString {
            append("{\"bridgeVersion\":")
            append(BRIDGE_VERSION)
            append(",\"type\":\"command\",\"commandId\":")
            append(json.encodeToString(String.serializer(), command.commandId))
            append(",\"action\":")
            append(action)
            append('}')
        }
        require(raw.toByteArray(Charsets.UTF_8).size <= MAX_COMMAND_FRAME_BYTES)
        return raw
    }

    private fun actionJson(kind: String, vararg values: Pair<String, String>): String =
        buildString {
            append("{\"kind\":")
            append(json.encodeToString(String.serializer(), kind))
            values.forEach { (key, value) ->
                append(',')
                append(json.encodeToString(String.serializer(), key))
                append(':')
                append(json.encodeToString(String.serializer(), value))
            }
            append('}')
        }

    private fun String.validCommandTaskId(): String {
        require(length in 1..256 && ID_PATTERN.matches(this))
        return this
    }

    private companion object {
        val COMMAND_ID_PATTERN = Regex("^[A-Za-z0-9-]{8,64}$")
        val BRIDGE_INSTANCE_ID_PATTERN = Regex("^[A-Za-z0-9-]{8,64}$")
        val ID_PATTERN = Regex("^[A-Za-z0-9][A-Za-z0-9._:@/\\-]{0,255}$")
    }
}
