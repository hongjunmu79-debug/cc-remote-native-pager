package dev.ccremote.pager.pairing

import dev.ccremote.pager.data.ServerEndpoint
import java.net.HttpURLConnection
import java.net.URI
import java.nio.charset.StandardCharsets
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class ClientPairingPayload(
    val endpoint: ServerEndpoint,
    val token: String,
    val machineId: String,
    val clientId: String,
) {
    companion object {
        private val machineIdPattern = Regex("[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")
        private val tokenPattern = Regex("[A-Za-z0-9_-]{32,128}")

        fun parse(raw: String, json: Json = Json): Result<ClientPairingPayload> = runCatching {
            require(raw.length in 80..4096) { "二维码内容无效" }
            val value = json.parseToJsonElement(raw).jsonObject
            require(value["v"]?.jsonPrimitive?.intOrNull == 1) { "二维码版本不受支持" }
            require(value["type"]?.jsonPrimitive?.contentOrNull == "cc_remote_client_pair") {
                "这不是 cc-remote 客户端配对码"
            }
            val endpoint = ServerEndpoint.parse(
                requireNotNull(value["relay"]?.jsonPrimitive?.contentOrNull),
            ).getOrThrow()
            val token = requireNotNull(value["token"]?.jsonPrimitive?.contentOrNull)
            val machineId = requireNotNull(value["machine_id"]?.jsonPrimitive?.contentOrNull)
            val clientId = requireNotNull(value["client_id"]?.jsonPrimitive?.contentOrNull)
            require(tokenPattern.matches(token)) { "配对凭据格式无效" }
            require(machineIdPattern.matches(machineId)) { "设备标识格式无效" }
            require(clientId.length in 1..128 && clientId.none { it.code < 32 }) {
                "客户端标识格式无效"
            }
            ClientPairingPayload(endpoint, token, machineId, clientId)
        }
    }
}

data class RedeemedClientPairing(
    val endpoint: ServerEndpoint,
    val machineId: String,
    val clientId: String,
    val cookie: String,
)

class ClientPairingClient {
    fun redeem(payload: ClientPairingPayload): Result<RedeemedClientPairing> = runCatching {
        val url = URI(payload.endpoint.url).resolve("api/client-pairing/redeem").toURL()
        val connection = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 10_000
            readTimeout = 10_000
            instanceFollowRedirects = false
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
        }
        try {
            val body = buildString {
                append("{\"token\":")
                append(Json.encodeToString(payload.token))
                append(",\"machine_id\":")
                append(Json.encodeToString(payload.machineId))
                append(",\"client_id\":")
                append(Json.encodeToString(payload.clientId))
                append('}')
            }.toByteArray(StandardCharsets.UTF_8)
            connection.setFixedLengthStreamingMode(body.size)
            connection.outputStream.use { it.write(body) }
            require(connection.responseCode == HttpURLConnection.HTTP_OK) {
                if (connection.responseCode == HttpURLConnection.HTTP_UNAUTHORIZED) {
                    "二维码已过期或已使用"
                } else {
                    "配对失败（HTTP ${connection.responseCode}）"
                }
            }
            val cookie = connection.headerFields.entries
                .firstOrNull { it.key?.equals("Set-Cookie", ignoreCase = true) == true }
                ?.value
                ?.firstOrNull { it.startsWith("cc_remote_session=") }
            require(!cookie.isNullOrBlank() && '\n' !in cookie && '\r' !in cookie) {
                "服务器未返回安全会话"
            }
            RedeemedClientPairing(
                endpoint = payload.endpoint,
                machineId = payload.machineId,
                clientId = payload.clientId,
                cookie = cookie,
            )
        } finally {
            connection.disconnect()
        }
    }
}
