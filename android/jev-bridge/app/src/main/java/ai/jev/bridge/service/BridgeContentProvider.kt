// Jev Bridge; AGPL-3.0-or-later. ADB access policy and API shape derived from Mobilerun Portal; see NOTICE.md.
package ai.jev.bridge.service

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Intent
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.Binder
import android.os.Process
import ai.jev.bridge.BuildConfig
import ai.jev.bridge.input.BridgeKeyboardIME
import org.json.JSONArray
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.util.Base64

class BridgeContentProvider : ContentProvider() {
    override fun onCreate() = true
    private fun checkCaller() {
        if (!ContentProviderAccessPolicy.isUidAllowed(Binder.getCallingUid(), Process.myUid())) {
            throw SecurityException("Jev Bridge accepts only this app, adb shell or root")
        }
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?,
                       selectionArgs: Array<out String>?, sortOrder: String?): Cursor {
        checkCaller()
        val response = try {
            val path = uri.path?.trim('/')
            val result: Any = when (path) {
                "ping" -> "pong"
                "version" -> JSONObject().put("name", "Jev Bridge").put("version", BuildConfig.VERSION_NAME).put("protocol", 1)
                "packages" -> packages()
                "http_info" -> BridgeAccessibilityService.instance?.httpInfo()
                    ?: error("Enable Jev Bridge accessibility service first")
                "state_full", "state", "a11y_tree_full", "a11y_tree", "phone_state" -> {
                    val service = BridgeAccessibilityService.instance
                        ?: error("Enable Jev Bridge accessibility service first")
                    val snapshot = service.snapshot(uri.getQueryParameter("filter") != "false")
                    when (path) {
                        "a11y_tree_full", "a11y_tree" -> snapshot.get("a11y_tree")
                        "phone_state" -> snapshot.get("phone_state")
                        else -> snapshot
                    }
                }
                else -> error("Unknown endpoint: $path")
            }
            JSONObject().put("status", "success").put("result", result)
        } catch (e: Exception) {
            JSONObject().put("status", "error").put("message", e.message ?: "Read failed")
        }
        return MatrixCursor(arrayOf("result")).apply { addRow(arrayOf(response.toString())) }
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri {
        checkCaller()
        val path = uri.path?.trim('/') ?: ""
        val result = if (path == "accessibility/set_text") {
            val packageName = values?.getAsString("package_name") ?: error("package_name is required")
            val resourceName = values.getAsString("resource_name") ?: error("resource_name is required")
            require(packageName.isNotBlank() && resourceName.isNotBlank() && !resourceName.contains('/')) { "Invalid resource identifier" }
            val resourceId = "$packageName:id/$resourceName"
            val encoded = values.getAsString("base64_text") ?: error("base64_text is required")
            require(encoded.length <= 65536) { "base64_text is too long" }
            val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(Base64.getDecoder().decode(encoded))).toString()
            val service = BridgeAccessibilityService.instance ?: error("Enable Jev Bridge accessibility service first")
            service.setText(resourceId, text)
            "accepted_unverified"
        } else BridgeKeyboardIME.execute(path,
            values?.getAsString("base64_text"), values?.getAsBoolean("clear") ?: true,
            values?.getAsInteger("key_code"))
        return Uri.Builder().scheme("content").authority(context!!.packageName).path("result")
            .appendQueryParameter("status", "success").appendQueryParameter("message", result).build()
    }

    private fun packages(): JSONArray {
        val manager = context!!.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        @Suppress("DEPRECATION")
        val activities = manager.queryIntentActivities(intent, 0)
        return JSONArray().apply {
            activities.distinctBy { it.activityInfo.packageName }.forEach {
                put(JSONObject().put("packageName", it.activityInfo.packageName)
                    .put("label", it.loadLabel(manager).toString()))
            }
        }
    }

    override fun getType(uri: Uri): String { checkCaller(); return "application/json" }
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int {
        checkCaller(); throw UnsupportedOperationException("Use keyboard endpoints")
    }
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int {
        checkCaller(); throw UnsupportedOperationException("Use keyboard/clear")
    }
}
