package dev.ccremote.pager.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerEndpointTest {
    @Test
    fun `accepts configured LAN origin and normalizes slash`() {
        val endpoint = ServerEndpoint.parse("http://192.168.3.4:8766").getOrThrow()
        assertEquals("http://192.168.3.4:8766/", endpoint.url)
        assertEquals("http://192.168.3.4:8766", endpoint.origin)
    }

    @Test
    fun `accepts secure origins`() {
        val endpoint = ServerEndpoint.parse("https://remote.example.com").getOrThrow()
        assertEquals("https://remote.example.com/", endpoint.url)
        assertEquals("https://remote.example.com", endpoint.origin)
    }

    @Test
    fun `rejects insecure untrusted path and credential variants`() {
        assertTrue(ServerEndpoint.parse("http://example.com").isFailure)
        assertTrue(ServerEndpoint.parse("https://example.com/app").isFailure)
        assertTrue(ServerEndpoint.parse("https://user:pass@example.com").isFailure)
        assertTrue(ServerEndpoint.parse("https://example.com/?token=secret").isFailure)
    }
}
