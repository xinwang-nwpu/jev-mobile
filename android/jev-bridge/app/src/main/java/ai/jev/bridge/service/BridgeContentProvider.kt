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
import android.util.Base64
import ai.jev.bridge.BuildConfig
import ai.jev.bridge.input.BridgeKeyboardIME
import ai.jev.bridge.input.TextInputResult
import org.json.JSONArray
import org.json.JSONObject

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
        val keyboard = BridgeKeyboardIME.instance ?: error("Jev Bridge Keyboard is not bound to an input field")
        val result = when (uri.path?.trim('/')) {
            "keyboard/input", "keyboard/clear" -> {
                val clearOnly = uri.path?.endsWith("/clear") == true
                val encoded = values?.getAsString("base64_text")
                require(clearOnly || encoded != null) { "base64_text is required" }
                require((encoded?.length ?: 0) <= 16000) { "Text exceeds the input limit" }
                val text = if (clearOnly) "" else String(Base64.decode(encoded, Base64.DEFAULT), Charsets.UTF_8)
                val status = keyboard.input(text, if (clearOnly) true else values?.getAsBoolean("clear") ?: true)
                when (status) {
                    TextInputResult.Verified -> "verified"
                    TextInputResult.AcceptedUnverified -> "accepted_unverified"
                    else -> error("Text input failed: $status; inspect the field before retrying")
                }
            }
            "keyboard/key" -> {
                val code = values?.getAsInteger("key_code") ?: error("key_code is required")
                check(keyboard.key(code)) { "Key event was rejected" }
                "key_sent"
            }
            else -> error("Unknown input endpoint")
        }
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
