// Jev Bridge; AGPL-3.0-or-later. See LICENSE.
package ai.jev.bridge.service

import ai.jev.bridge.input.BridgeKeyboardIME
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.Closeable
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import kotlin.concurrent.thread

/** One local ADB client; no network discovery or public listener. */
internal class BridgeHttpServer(private val snapshot: (Boolean) -> JSONObject) : Closeable {
    private val token = Base64.getUrlEncoder().withoutPadding()
        .encodeToString(ByteArray(32).also { SecureRandom().nextBytes(it) })
    private val listener = ServerSocket(0, 4, InetAddress.getByName("127.0.0.1"))
    @Volatile private var client: Socket? = null
    private val worker = thread(name = "jev-bridge-http", isDaemon = true) {
        // ponytail: serial requests suffice for one agent; use a bounded pool if multiple clients are needed.
        while (!listener.isClosed) {
            try {
                listener.accept().use { socket ->
                    client = socket
                    socket.soTimeout = 3000
                    runCatching { handle(socket) }
                    client = null
                }
            } catch (_: Exception) {
                if (listener.isClosed) break
            }
        }
    }

    fun info(): JSONObject = JSONObject().put("port", listener.localPort).put("token", token).put("protocol", 1)

    private fun handle(socket: Socket) {
        val input = BufferedInputStream(socket.getInputStream())
        var headerBytes = 0
        fun line(): String {
            val bytes = ArrayList<Byte>()
            while (true) {
                val value = input.read()
                require(value >= 0 && ++headerBytes <= 8192) { "Invalid HTTP headers" }
                if (value == 10) break
                bytes.add(value.toByte())
            }
            return String(bytes.toByteArray(), Charsets.US_ASCII).trimEnd('\r')
        }
        val request = line().split(' ')
        if (request.size != 3 || request[2] != "HTTP/1.1") {
            respond(socket, 400, error("Invalid request")); return
        }
        val headers = mutableMapOf<String, String>()
        while (true) {
            val row = line()
            if (row.isEmpty()) break
            val colon = row.indexOf(':')
            require(colon > 0) { "Invalid header" }
            val key = row.substring(0, colon).lowercase()
            require(key !in headers) { "Duplicate header" }
            headers[key] = row.substring(colon + 1).trim()
        }
        if (!MessageDigest.isEqual((headers["authorization"] ?: "").toByteArray(), "Bearer $token".toByteArray())) {
            respond(socket, 401, error("Unauthorized")); return
        }
        if ("transfer-encoding" in headers) {
            respond(socket, 400, error("Chunked requests are unsupported")); return
        }
        val length = headers["content-length"]?.toIntOrNull() ?: 0
        if (length !in 0..24000) {
            respond(socket, 413, error("Request body exceeds limit")); return
        }
        val path = request[1]
        if (request[0] == "GET") {
            val result = try {
                when (path) {
                    "/ping" -> "pong"
                    "/state_full", "/state_full?filter=false" -> snapshot(!path.endsWith("filter=false"))
                    else -> { respond(socket, 404, error("Unknown endpoint")); return }
                }
            } catch (e: Exception) {
                respond(socket, 503, error(e.message ?: "State unavailable")); return
            }
            respond(socket, 200, success(result)); return
        }
        if (request[0] != "POST" || path !in setOf("/keyboard/input", "/keyboard/clear", "/keyboard/key")) {
            respond(socket, 404, error("Unknown endpoint")); return
        }
        val body = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val count = input.read(body, offset, length - offset)
            require(count > 0) { "Incomplete request body" }
            offset += count
        }
        val values = try {
            JSONObject(String(body, Charsets.UTF_8)).also {
                require(!it.has("clear") || it.get("clear") is Boolean) { "clear must be boolean" }
                require(!it.has("base64_text") || it.get("base64_text") is String) { "base64_text must be string" }
                require(!it.has("key_code") || it.get("key_code") is Int) { "key_code must be integer" }
            }
        } catch (e: Exception) {
            respond(socket, 400, error(e.message ?: "Invalid JSON")); return
        }
        // Errors after entering the editor may follow a partial commit. Clients must observe, not replay.
        try {
            val result = BridgeKeyboardIME.execute(path.trim('/'),
                if (values.has("base64_text")) values.getString("base64_text") else null,
                values.optBoolean("clear", true),
                if (values.has("key_code")) values.getInt("key_code") else null)
            respond(socket, 200, success(result))
        } catch (e: Exception) {
            respond(socket, 409, error(e.message ?: "Input outcome unknown; inspect the field"))
        }
    }

    private fun success(result: Any) = JSONObject().put("status", "success").put("result", result)
    private fun error(message: String) = JSONObject().put("status", "error").put("message", message)
    private fun respond(socket: Socket, code: Int, response: JSONObject) {
        val data = response.toString().toByteArray(Charsets.UTF_8)
        val header = "HTTP/1.1 $code Result\r\nContent-Type: application/json; charset=utf-8\r\n" +
            "Content-Length: ${data.size}\r\nConnection: close\r\n\r\n"
        socket.getOutputStream().apply { write(header.toByteArray(Charsets.US_ASCII)); write(data); flush() }
    }

    override fun close() {
        listener.close()
        runCatching { client?.close() }
    }
}
