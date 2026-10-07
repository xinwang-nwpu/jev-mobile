package ai.jev.bridge.service

import ai.jev.bridge.input.BridgeKeyboardIME
import io.mockk.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.Socket

class BridgeHttpServerTest {
    private fun request(server: BridgeHttpServer, method: String, path: String,
                        body: String = "", authorized: Boolean = true, extra: String = ""): Pair<Int, JSONObject> {
        val info = server.info()
        val bytes = body.toByteArray(Charsets.UTF_8)
        return Socket("127.0.0.1", info.getInt("port")).use { socket ->
            socket.soTimeout = 3000
            val auth = if (authorized) "Authorization: Bearer ${info.getString("token")}\r\n" else ""
            val headers = "$method $path HTTP/1.1\r\nHost: localhost\r\n$auth$extra" +
                "Content-Length: ${bytes.size}\r\n\r\n"
            socket.getOutputStream().apply { write(headers.toByteArray()); write(bytes); flush() }
            val result = socket.getInputStream().readBytes().toString(Charsets.UTF_8)
            result.substringBefore('\r').split(' ')[1].toInt() to JSONObject(result.substringAfter("\r\n\r\n"))
        }
    }

    @Test fun authenticatedSnapshotAndInputUseExistingHandlersAndByteLength() {
        mockkObject(BridgeKeyboardIME.Companion)
        try {
            every { BridgeKeyboardIME.execute("keyboard/input", "5L2g5aW9", false, null) } returns "verified"
            BridgeHttpServer { filter -> JSONObject().put("filtered", filter) }.use { server ->
                assertEquals(401, request(server, "GET", "/state_full", authorized = false).first)
                val state = request(server, "GET", "/state_full?filter=false")
                assertEquals(200, state.first)
                assertFalse(state.second.getJSONObject("result").getBoolean("filtered"))
                val body = """{"base64_text":"5L2g5aW9","clear":false,"note":"你好🌟"}"""
                val input = request(server, "POST", "/keyboard/input", body)
                assertEquals(200, input.first)
                assertEquals("verified", input.second.getString("result"))
                verify(exactly = 1) { BridgeKeyboardIME.execute("keyboard/input", "5L2g5aW9", false, null) }
            }
        } finally { unmockkAll() }
    }

    @Test fun rejectedRequestsNeverReachTheEditorAndUnknownCommitIsAnError() {
        mockkObject(BridgeKeyboardIME.Companion)
        try {
            every { BridgeKeyboardIME.execute(any(), any(), any(), any()) } throws IllegalStateException("CommitOutcomeUnknown")
            val server = BridgeHttpServer { JSONObject() }
            val port = server.info().getInt("port")
            server.use {
                assertEquals(401, request(server, "POST", "/keyboard/input", "{}", false).first)
                assertEquals(404, request(server, "POST", "/unsupported", "{}").first)
                assertEquals(400, request(server, "POST", "/keyboard/input", """{"clear":"false"}""").first)
                assertEquals(400, request(server, "POST", "/keyboard/input", "{}", extra = "Transfer-Encoding: chunked\r\n").first)
                assertEquals(413, request(server, "POST", "/keyboard/input", "x".repeat(24001)).first)
                verify(exactly = 0) { BridgeKeyboardIME.execute(any(), any(), any(), any()) }
                val failed = request(server, "POST", "/keyboard/input", """{"base64_text":"5L2g5aW9"}""")
                assertEquals(409, failed.first)
                assertEquals("error", failed.second.getString("status"))
                verify(exactly = 1) { BridgeKeyboardIME.execute(any(), any(), any(), any()) }
            }
            assertThrows(java.io.IOException::class.java) { Socket("127.0.0.1", port).close() }
        } finally { unmockkAll() }
    }
}
