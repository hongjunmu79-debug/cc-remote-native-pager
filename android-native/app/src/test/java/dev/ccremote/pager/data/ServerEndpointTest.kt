package dev.ccremote.pager.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerEndpointTest {
    @Test
    fun `accepts private LAN origin and normalizes slash`() {
        val endpoint = ServerEndpoint.parse("http://192.168.1.23:8766").getOrThrow()
        assertEquals("http://192.168.1.23:8766/", endpoint.url)
        assertEquals("http://192.168.1.23:8766", endpoint.origin)
    }

    @Test
    fun `accepts all private and loopback IP ranges over cleartext`() {
        assertTrue(ServerEndpoint.parse("http://10.0.0.8").isSuccess)
        assertTrue(ServerEndpoint.parse("http://172.16.5.5:8766/").isSuccess)
        assertTrue(ServerEndpoint.parse("http://172.31.255.255").isSuccess)
        assertTrue(ServerEndpoint.parse("http://192.168.0.1").isSuccess)
        assertTrue(ServerEndpoint.parse("http://127.0.0.1:8765").isSuccess)
    }

    @Test
    fun `accepts secure origins`() {
        val endpoint = ServerEndpoint.parse("https://remote.example.com").getOrThrow()
        assertEquals("https://remote.example.com/", endpoint.url)
        assertEquals("https://remote.example.com", endpoint.origin)

        val withPort = ServerEndpoint.parse("https://remote.example.com:8443").getOrThrow()
        assertEquals("https://remote.example.com:8443/", withPort.url)
        assertEquals("https://remote.example.com:8443", withPort.origin)
    }

    @Test
    fun `rejects insecure untrusted and malformed variants`() {
        // Public HTTP origins (hostnames and public IPs) are always rejected.
        assertTrue(ServerEndpoint.parse("http://example.com").isFailure)
        assertTrue(ServerEndpoint.parse("http://203.0.113.7").isFailure)
        assertTrue(ServerEndpoint.parse("http://8.8.8.8").isFailure)
        // Hostnames are not private IP literals even when they resolve locally.
        assertTrue(ServerEndpoint.parse("http://localhost:8765").isFailure)
        // Link-local and public segments of private-looking ranges are rejected.
        assertTrue(ServerEndpoint.parse("http://169.254.1.1").isFailure)
        assertTrue(ServerEndpoint.parse("http://172.32.0.1").isFailure)
        // Non-root paths, credentials, query strings, and fragments.
        assertTrue(ServerEndpoint.parse("https://example.com/app").isFailure)
        assertTrue(ServerEndpoint.parse("http://192.168.1.23/app").isFailure)
        assertTrue(ServerEndpoint.parse("https://user:pass@example.com").isFailure)
        assertTrue(ServerEndpoint.parse("https://example.com/?token=secret").isFailure)
        assertTrue(ServerEndpoint.parse("https://example.com/#frag").isFailure)
        // Unsupported schemes.
        assertTrue(ServerEndpoint.parse("ftp://192.168.1.23").isFailure)
        assertTrue(ServerEndpoint.parse("file:///etc/passwd").isFailure)
    }

    @Test
    fun `ip literal classification covers private and loopback ranges`() {
        assertTrue(isPrivateOrLocalIpLiteral("10.1.2.3"))
        assertTrue(isPrivateOrLocalIpLiteral("172.16.0.1"))
        assertTrue(isPrivateOrLocalIpLiteral("172.31.255.255"))
        assertTrue(isPrivateOrLocalIpLiteral("192.168.255.255"))
        assertTrue(isPrivateOrLocalIpLiteral("127.0.0.1"))
        assertTrue(!isPrivateOrLocalIpLiteral("172.32.0.1"))
        assertTrue(!isPrivateOrLocalIpLiteral("172.15.0.1"))
        assertTrue(!isPrivateOrLocalIpLiteral("8.8.8.8"))
        assertTrue(!isPrivateOrLocalIpLiteral("203.0.113.7"))
        assertTrue(!isPrivateOrLocalIpLiteral("example.com"))
        assertTrue(!isPrivateOrLocalIpLiteral("localhost"))
        assertTrue(!isPrivateOrLocalIpLiteral("192.168.1"))
        assertTrue(!isPrivateOrLocalIpLiteral("192.168.1.999"))
        assertTrue(!isPrivateOrLocalIpLiteral("::1"))
        assertTrue(!isPrivateOrLocalIpLiteral(null))
    }
}
