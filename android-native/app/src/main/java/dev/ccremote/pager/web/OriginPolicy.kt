package dev.ccremote.pager.web

import dev.ccremote.pager.data.ServerEndpoint

/**
 * Exact-origin enforcement for the pager WebView.
 *
 * Navigation and every subresource must match the selected endpoint's exact
 * origin (scheme, normalized host, effective port). Off-origin HTTPS and
 * off-origin private HTTP are both rejected: the endpoint origin is the only
 * trust boundary, so a DNS-rebinding or cross-site fetch cannot gain bridge
 * access. Only `http`/`https` are ever handed to the system browser; arbitrary
 * schemes are never dispatched.
 *
 * The object is pure and JVM-only (it takes scheme/host/port primitives, not
 * `android.net.Uri`) so the same rules that gate the live WebView are exercised
 * by plain JUnit tests.
 */
object OriginPolicy {
    /** Effective default port for a scheme, or null for unsupported schemes. */
    fun defaultPortFor(scheme: String?): Int? = when (scheme) {
        "https" -> 443
        "http" -> 80
        else -> null
    }

    /**
     * Normalized origin string for a (scheme, host, port) triple, or null when
     * it cannot form a real origin (missing/blank host, unsupported scheme,
     * out-of-range port). The host is lowercased and a trailing dot is dropped;
     * an explicitly-supplied default port is elided so `https://x:443` and
     * `https://x` are the same origin.
     */
    fun originOf(scheme: String?, host: String?, port: Int): String? {
        if (scheme == null) return null
        val defaultPort = defaultPortFor(scheme) ?: return null
        val normalizedHost = host?.trim()?.lowercase()?.trimEnd('.')
        if (normalizedHost.isNullOrEmpty()) return null
        val effective = if (port != -1) port else defaultPort
        if (effective !in 1..65535) return null
        return buildString {
            append(scheme)
            append("://")
            append(normalizedHost)
            if (effective != defaultPort) {
                append(':')
                append(effective)
            }
        }
    }

    /**
     * True when the candidate (scheme, host, port) is exactly [endpoint]'s
     * origin. The candidate and the endpoint are both normalized through
     * [originOf], so default-port elision and host case cannot cause a bypass.
     */
    fun isSameOrigin(
        endpoint: ServerEndpoint,
        scheme: String?,
        host: String?,
        port: Int,
    ): Boolean = originOf(scheme, host, port) == endpoint.origin

    /** Only http/https may be opened in the system browser. Anything else
     *  (`javascript:`, `intent:`, `file:`, `sms:`, …) is never dispatched. */
    fun isSafeExternalScheme(scheme: String?): Boolean =
        scheme == "http" || scheme == "https"
}
