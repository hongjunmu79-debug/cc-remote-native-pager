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
    val endpoint: ServerEndpoint,
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
                require(uri.host == "192.168.3.4") {
                    "明文 HTTP 仅允许当前受信任局域网主机 192.168.3.4"
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

        val Default: ServerEndpoint by lazy {
            parse(BuildConfig.DEFAULT_SERVER_URL).getOrThrow()
        }
    }
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
