package dev.ccremote.pager.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientPairingPayloadTest {
    @Test
    fun `parses scoped pairing json without putting the token in a url`() {
        val token = "a".repeat(43)
        val parsed = ClientPairingPayload.parse(
            """{"v":1,"type":"cc_remote_client_pair","relay":"https://remote.example","token":"$token","machine_id":"desktop-1","client_id":"paired-123"}""",
        ).getOrThrow()
        assertEquals("https://remote.example/", parsed.endpoint.url)
        assertEquals(token, parsed.token)
        assertEquals("desktop-1", parsed.machineId)
        assertEquals("paired-123", parsed.clientId)
    }

    @Test
    fun `rejects url query credentials and malformed scope`() {
        assertTrue(ClientPairingPayload.parse(
            "https://remote.example/?token=${"a".repeat(43)}",
        ).isFailure)
        assertTrue(ClientPairingPayload.parse(
            """{"v":1,"type":"cc_remote_client_pair","relay":"https://remote.example","token":"short","machine_id":"bad machine","client_id":"x"}""",
        ).isFailure)
    }
}
