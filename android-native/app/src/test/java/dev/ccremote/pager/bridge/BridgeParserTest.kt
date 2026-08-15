package dev.ccremote.pager.bridge

import dev.ccremote.pager.domain.PagerActivity
import dev.ccremote.pager.domain.PagerCapability
import dev.ccremote.pager.domain.PagerLifecycle
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeParserTest {
    private val parser = BridgeParser()

    @Test
    fun `parses bounded authenticated snapshot`() {
        val event = parser.parse(validSnapshot).getOrThrow() as BridgeInboundEvent.Snapshot
        val task = event.value.tasks.single()

        assertEquals(7, event.value.sequence)
        assertEquals("machine-1", event.value.machineId)
        assertEquals(PagerLifecycle.RUNNING, task.lifecycle)
        assertEquals(PagerActivity.TESTING, task.activity)
        assertTrue(PagerCapability.INTERRUPT in task.capabilities)
    }

    @Test
    fun `rejects protocol downgrade and unauthenticated frames`() {
        assertTrue(parser.parse(validSnapshot.replace("\"bridgeVersion\":1", "\"bridgeVersion\":0")).isFailure)
        assertTrue(parser.parse(validSnapshot.replace("\"authenticated\"", "\"anonymous\"")).isFailure)
    }

    @Test
    fun `rejects oversized inbound and answer frames`() {
        assertTrue(parser.parse(" ".repeat(MAX_BRIDGE_FRAME_BYTES + 1)).isFailure)
        val command = PagerCommand(
            commandId = "command-1234",
            action = PagerCommandAction.AnswerQuestion("task-1", "x".repeat(8 * 1024 + 1)),
        )
        assertTrue(runCatching { parser.encodeCommand(command) }.isFailure)
    }

    @Test
    fun `encodes commands with JSON escaping`() {
        val encoded = parser.encodeCommand(
            PagerCommand(
                commandId = "command-1234",
                action = PagerCommandAction.AnswerQuestion("task-1", "line one\n\"quoted\""),
            ),
        )

        assertTrue(encoded.contains("\\n\\\"quoted\\\""))
        assertFalse(encoded.contains("line one\n"))
    }

    private companion object {
        val validSnapshot = """
            {
              "bridgeVersion":1,
              "type":"snapshot",
              "bridgeInstanceId":"instance-1234",
              "sequence":7,
              "emittedAt":1700000000000,
              "payload":{
                "auth":"authenticated",
                "connection":"connected",
                "wrapperOnline":true,
                "machineId":"machine-1",
                "focusedTaskId":"task-1",
                "tasks":[{
                  "id":"task-1",
                  "engine":"codex",
                  "projectName":"cc-remote",
                  "title":"Build native pager",
                  "lifecycle":"running",
                  "activity":"testing",
                  "latestStep":"Running bridge tests",
                  "startedAt":1699999990000,
                  "updatedAt":1700000000000,
                  "pinned":false,
                  "focused":true,
                  "capabilities":["openChat","interrupt","pin"],
                  "subagents":[]
                }]
              }
            }
        """.trimIndent()
    }
}
