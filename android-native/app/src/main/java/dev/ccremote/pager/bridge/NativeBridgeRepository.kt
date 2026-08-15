package dev.ccremote.pager.bridge

import dev.ccremote.pager.domain.PagerSnapshot
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

data class NativeBridgeState(
    val snapshot: PagerSnapshot? = null,
    val bridgeInstanceId: String? = null,
    val lastFrameAtElapsed: Long? = null,
    val lastSequence: Long = 0,
    val rejectedFrames: Int = 0,
    val lastError: String? = null,
)

data class NativeCommandAck(
    val commandId: String,
    val accepted: Boolean,
    val message: String?,
)

class NativeBridgeRepository internal constructor(
    private val parser: BridgeParser = BridgeParser(),
    private val elapsedRealtime: () -> Long,
) {
    private val mutableState = MutableStateFlow(NativeBridgeState())
    val state: StateFlow<NativeBridgeState> = mutableState.asStateFlow()

    private val mutableAcks = MutableSharedFlow<NativeCommandAck>(extraBufferCapacity = 32)
    val acknowledgements: SharedFlow<NativeCommandAck> = mutableAcks.asSharedFlow()

    fun accept(raw: String) {
        parser.parse(raw).onSuccess(::acceptEvent).onFailure { error ->
            mutableState.value = mutableState.value.copy(
                rejectedFrames = mutableState.value.rejectedFrames + 1,
                lastError = error.message?.take(240) ?: "Bridge frame rejected",
            )
        }
    }

    fun encode(command: PagerCommand): Result<String> = runCatching {
        parser.encodeCommand(command)
    }

    fun reset() {
        mutableState.value = NativeBridgeState()
    }

    private fun acceptEvent(event: BridgeInboundEvent) {
        val now = elapsedRealtime()
        when (event) {
            is BridgeInboundEvent.Snapshot -> {
                val instanceChanged = event.value.bridgeInstanceId !=
                    mutableState.value.bridgeInstanceId
                if (!instanceChanged && event.value.sequence <= mutableState.value.lastSequence) {
                    return
                }
                mutableState.value = mutableState.value.copy(
                    snapshot = event.value,
                    bridgeInstanceId = event.value.bridgeInstanceId,
                    lastFrameAtElapsed = now,
                    lastSequence = event.value.sequence,
                    lastError = null,
                )
            }
            is BridgeInboundEvent.Heartbeat -> {
                if (event.bridgeInstanceId != mutableState.value.bridgeInstanceId) return
                mutableState.value = mutableState.value.copy(
                    lastFrameAtElapsed = now,
                    lastError = null,
                )
            }
            is BridgeInboundEvent.CommandAck -> {
                if (event.bridgeInstanceId != mutableState.value.bridgeInstanceId) return
                mutableState.value = mutableState.value.copy(
                    lastFrameAtElapsed = now,
                    lastError = null,
                )
                mutableAcks.tryEmit(
                    NativeCommandAck(event.commandId, event.accepted, event.message),
                )
            }
        }
    }
}
