package dev.ccremote.pager.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeBridgeRepositoryTest {
    @Test
    fun `ignores stale snapshots and records rejected frames`() {
        var elapsed = 10L
        val repository = NativeBridgeRepository(elapsedRealtime = { elapsed })

        repository.accept(snapshot(sequence = 2, machine = "new"))
        elapsed = 20L
        repository.accept(snapshot(sequence = 1, machine = "stale"))
        repository.accept("not-json")

        assertEquals("new", repository.state.value.snapshot?.machineId)
        assertEquals(2, repository.state.value.lastSequence)
        assertEquals(10L, repository.state.value.lastFrameAtElapsed)
        assertEquals(1, repository.state.value.rejectedFrames)
    }

    @Test
    fun `heartbeat refreshes freshness without replacing snapshot`() {
        var elapsed = 10L
        val repository = NativeBridgeRepository(elapsedRealtime = { elapsed })
        repository.accept(snapshot(sequence = 2, machine = "machine"))

        elapsed = 50L
        repository.accept("""{"bridgeVersion":1,"type":"heartbeat","bridgeInstanceId":"instance-1234","emittedAt":50}""")

        assertEquals(50L, repository.state.value.lastFrameAtElapsed)
        assertEquals("machine", repository.state.value.snapshot?.machineId)
        assertNull(repository.state.value.lastError)
    }

    @Test
    fun `accepts reset sequence from a new page instance`() {
        val repository = NativeBridgeRepository(elapsedRealtime = { 10L })
        repository.accept(snapshot(sequence = 9, machine = "old", instance = "instance-old"))
        repository.accept(snapshot(sequence = 1, machine = "new", instance = "instance-new"))

        assertEquals("new", repository.state.value.snapshot?.machineId)
        assertEquals(1, repository.state.value.lastSequence)
        assertEquals("instance-new", repository.state.value.bridgeInstanceId)
    }

    private fun snapshot(
        sequence: Long,
        machine: String,
        instance: String = "instance-1234",
    ): String = """
        {
          "bridgeVersion":1,
          "type":"snapshot",
          "bridgeInstanceId":"$instance",
          "sequence":$sequence,
          "emittedAt":100,
          "payload":{
            "auth":"authenticated",
            "connection":"connected",
            "wrapperOnline":true,
            "machineId":"$machine",
            "tasks":[]
          }
        }
    """.trimIndent()
}
