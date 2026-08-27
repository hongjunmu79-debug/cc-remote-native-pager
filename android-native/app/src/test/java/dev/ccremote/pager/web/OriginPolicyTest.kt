package dev.ccremote.pager.web

import dev.ccremote.pager.data.ServerEndpoint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OriginPolicyTest {
    private val lanHttp = ServerEndpoint.parse("http://192.168.1.23:8766").getOrThrow()
    private val secureHttps = ServerEndpoint.parse("https://remote.example.com").getOrThrow()
    private val secureHttpsPort = ServerEndpoint.parse("https://remote.example.com:8443").getOrThrow()

    @Test
    fun `same origin for the exact endpoint host scheme and port`() {
        assertTrue(OriginPolicy.isSameOrigin(lanHttp, "http", "192.168.1.23", 8766))
        assertTrue(OriginPolicy.isSameOrigin(secureHttps, "https", "remote.example.com", -1))
        assertTrue(OriginPolicy.isSameOrigin(secureHttps, "https", "remote.example.com", 443))
        assertTrue(OriginPolicy.isSameOrigin(secureHttpsPort, "https", "remote.example.com", 8443))
    }

    @Test
    fun `default ports are equivalent to an omitted port`() {
        assertEquals(
            OriginPolicy.originOf("https", "remote.example.com", -1),
            OriginPolicy.originOf("https", "remote.example.com", 443),
        )
        assertEquals(
            OriginPolicy.originOf("http", "192.168.1.23", -1),
            OriginPolicy.originOf("http", "192.168.1.23", 80),
        )
        assertTrue(OriginPolicy.isSameOrigin(secureHttps, "https", "remote.example.com", 443))
    }

    @Test
    fun `explicit non-default port changes the origin`() {
        assertFalse(OriginPolicy.isSameOrigin(secureHttps, "https", "remote.example.com", 8443))
        assertFalse(OriginPolicy.isSameOrigin(secureHttpsPort, "https", "remote.example.com", 443))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "192.168.1.23", 8765))
    }

    @Test
    fun `off-origin HTTPS is rejected`() {
        // Same scheme, different host — must not be the endpoint origin.
        assertFalse(OriginPolicy.isSameOrigin(secureHttps, "https", "evil.example.net", 443))
        assertFalse(OriginPolicy.isSameOrigin(secureHttps, "https", "remote.example.com.evil.net", 443))
        // Same host, different scheme — https endpoint must not accept http.
        assertFalse(OriginPolicy.isSameOrigin(secureHttps, "http", "remote.example.com", 443))
        assertFalse(OriginPolicy.isSameOrigin(secureHttpsPort, "http", "remote.example.com", 8443))
    }

    @Test
    fun `off-origin private HTTP is rejected`() {
        // Another RFC1918 host is a different origin, even though the payload
        // itself may be cleartext-HTTP-capable.
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "192.168.1.99", 8766))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "10.0.0.5", 8766))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "192.168.1.23", 80))
    }

    @Test
    fun `normalized host ignores case and trailing dot`() {
        assertTrue(OriginPolicy.isSameOrigin(secureHttps, "https", "REMOTE.EXAMPLE.COM.", -1))
        assertEquals("https://remote.example.com", OriginPolicy.originOf("https", "Remote.Example.COM.", 443))
    }

    @Test
    fun `unsafe schemes never match an origin and are not externally openable`() {
        for (scheme in listOf("javascript", "intent", "file", "content", "sms", "tel", "data", "about")) {
            assertNull(OriginPolicy.originOf(scheme, "x", -1))
            assertFalse(OriginPolicy.isSameOrigin(lanHttp, scheme, "192.168.1.23", 8766))
            assertFalse(OriginPolicy.isSafeExternalScheme(scheme))
        }
        assertTrue(OriginPolicy.isSafeExternalScheme("http"))
        assertTrue(OriginPolicy.isSafeExternalScheme("https"))
        assertFalse(OriginPolicy.isSafeExternalScheme(null))
    }

    @Test
    fun `missing or malformed origins never match`() {
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, null, "192.168.1.23", 8766))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", null, 8766))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "  ", 8766))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "192.168.1.23", 0))
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "http", "192.168.1.23", 70000))
        assertNull(OriginPolicy.originOf("https", "", 443))
        assertNull(OriginPolicy.originOf("ftp", "host", 21))
    }

    @Test
    fun `bridge is scoped to the endpoint exact origin`() {
        // The WebView bridge restrict-origin set is exactly the endpoint origin.
        assertEquals("http://192.168.1.23:8766", lanHttp.origin)
        assertEquals("https://remote.example.com", secureHttps.origin)
        assertEquals("https://remote.example.com:8443", secureHttpsPort.origin)
        // A candidate that would be accepted under the old "any HTTPS / any
        // private HTTP" rule must now be rejected.
        assertFalse(OriginPolicy.isSameOrigin(lanHttp, "https", "any-other-host.example", 443))
        assertFalse(OriginPolicy.isSameOrigin(secureHttps, "http", "10.1.2.3", 80))
    }
}
