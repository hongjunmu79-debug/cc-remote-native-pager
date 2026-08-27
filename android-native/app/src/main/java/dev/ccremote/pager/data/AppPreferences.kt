package dev.ccremote.pager.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dev.ccremote.pager.BuildConfig
import java.io.IOException
import java.net.URI
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

private val Context.nativePagerDataStore by preferencesDataStore("native_pager")

data class PagerPreferences(
    val endpoint: ServerEndpoint?,
    val seenRevisions: Map<String, String>,
    val feedbackEnabled: Boolean,
)

data class ServerEndpoint private constructor(
    val url: String,
    val origin: String,
) {
    companion object {
        fun parse(value: String): Result<ServerEndpoint> = runCatching {
            val normalized = if (value.endsWith('/')) value else "$value/"
            val uri = URI(normalized)
            require(uri.scheme == "https" || uri.scheme == "http") {
                "地址必须使用 HTTPS 或受信任的局域网 HTTP"
            }
            require(uri.host?.isNotBlank() == true && uri.userInfo == null) {
                "地址缺少有效主机或包含用户信息"
            }
            require(uri.rawQuery == null && uri.rawFragment == null) {
                "地址不能包含查询参数或片段"
            }
            require(uri.path == null || uri.path == "/") {
                "当前版本只支持服务器根地址"
            }
            if (uri.scheme == "http") {
                // Cleartext HTTP is accepted only for explicit private/local
                // IPv4 literals (RFC1918 10/8, 172.16/12, 192.168/16, loopback).
                // Public HTTP, hostnames, IPv6, userinfo, query strings and
                // fragments are rejected before a page ever loads.
                require(isPrivateOrLocalIpLiteral(uri.host)) {
                    "明文 HTTP 仅允许私有或本地 IP 地址（如 192.168.x.x）"
                }
            }
            val defaultPort = if (uri.scheme == "https") 443 else 80
            val origin = buildString {
                append(uri.scheme)
                append("://")
                append(uri.host)
                if (uri.port != -1 && uri.port != defaultPort) {
                    append(':')
                    append(uri.port)
                }
            }
            ServerEndpoint(normalized, origin)
        }

        /** The build-time default endpoint, or null when the build ships no
         *  default (first launch must let the user enter one). */
        val Default: ServerEndpoint? by lazy {
            BuildConfig.DEFAULT_SERVER_URL.takeIf { it.isNotBlank() }
                ?.let { parse(it).getOrNull() }
        }
    }
}

/** True when [host] is an IPv4 literal in a private/local range: 10/8,
 *  172.16/12, 192.168/16, or 127/8 loopback. Hostnames and IPv6 literals
 *  return false so cleartext HTTP cannot target a public host. */
internal fun isPrivateOrLocalIpLiteral(host: String?): Boolean {
    val octets = host?.split('.')?.map(String::toIntOrNull) ?: return false
    if (octets.size != 4 || octets.any { it == null || it !in 0..255 }) return false
    val a = octets[0]!!
    val b = octets[1]!!
    return a == 10 ||
        (a == 172 && b in 16..31) ||
        (a == 192 && b == 168) ||
        a == 127
}
class AppPreferences(
    private val context: Context,
    private val json: Json = Json,
) {
    val values: Flow<PagerPreferences> = context.nativePagerDataStore.data
        .catch { error ->
            if (error is IOException) emit(androidx.datastore.preferences.core.emptyPreferences())
            else throw error
        }
        .map { preferences ->
            val endpoint = preferences[SERVER_URL]
                ?.let(ServerEndpoint::parse)?.getOrNull()
                ?: ServerEndpoint.Default
            val seen = preferences[SEEN_REVISIONS]?.let(::decodeSeen).orEmpty()
            PagerPreferences(
                endpoint = endpoint,
                seenRevisions = seen,
                feedbackEnabled = preferences[FEEDBACK_ENABLED] ?: true,
            )
        }

    suspend fun setServerUrl(value: String): Result<ServerEndpoint> {
        val endpoint = ServerEndpoint.parse(value)
        endpoint.onSuccess { accepted ->
            context.nativePagerDataStore.edit { it[SERVER_URL] = accepted.url }
        }
        return endpoint
    }

    suspend fun setFeedbackEnabled(enabled: Boolean) {
        context.nativePagerDataStore.edit { it[FEEDBACK_ENABLED] = enabled }
    }

    suspend fun markSeen(taskId: String, revision: String) {
        require(taskId.length in 1..256 && revision.length in 1..512)
        context.nativePagerDataStore.edit { preferences ->
            val values = LinkedHashMap(
                preferences[SEEN_REVISIONS]?.let(::decodeSeen).orEmpty(),
            )
            values.remove(taskId)
            values[taskId] = revision
            while (values.size > MAX_SEEN_REVISIONS) {
                values.remove(values.keys.first())
            }
            preferences[SEEN_REVISIONS] = json.encodeToString(SEEN_SERIALIZER, values)
        }
    }

    private fun decodeSeen(raw: String): Map<String, String> = runCatching {
        json.decodeFromString(SEEN_SERIALIZER, raw).entries
            .filter { (key, value) -> key.length in 1..256 && value.length in 1..512 }
            .takeLast(MAX_SEEN_REVISIONS)
            .associateTo(linkedMapOf()) { it.toPair() }
    }.getOrDefault(emptyMap())

    private companion object {
        val SERVER_URL = stringPreferencesKey("server_url")
        val SEEN_REVISIONS = stringPreferencesKey("seen_revisions")
        val FEEDBACK_ENABLED = booleanPreferencesKey("feedback_enabled")
        val SEEN_SERIALIZER = MapSerializer(String.serializer(), String.serializer())
        const val MAX_SEEN_REVISIONS = 256
    }
}
