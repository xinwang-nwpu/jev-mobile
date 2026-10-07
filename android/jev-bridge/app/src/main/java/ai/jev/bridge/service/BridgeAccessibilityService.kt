// Jev Bridge; AGPL-3.0-or-later. State assembly adapted from Mobilerun Portal; see NOTICE.md.
package ai.jev.bridge.service

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.graphics.Point
import android.graphics.Rect
import android.os.Build
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import ai.jev.bridge.core.AccessibilityRootResolver
import ai.jev.bridge.core.AccessibilityTreeBuilder
import org.json.JSONArray
import org.json.JSONObject

class BridgeAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var instance: BridgeAccessibilityService? = null
            private set
    }
    @Volatile private var lastPackage: String? = null
    @Volatile private var lastActivity: String? = null
    @Volatile private var httpServer: BridgeHttpServer? = null

    override fun onServiceConnected() { instance = this }

    @Synchronized internal fun httpInfo(): JSONObject {
        val server = httpServer ?: BridgeHttpServer(::snapshot).also { httpServer = it }
        return server.info()
    }

    @Synchronized private fun stopHttp() {
        httpServer?.close()
        httpServer = null
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            lastPackage = event.packageName?.toString()
            lastActivity = event.className?.toString()
        }
    }

    override fun onInterrupt() {}
    override fun onUnbind(intent: Intent?): Boolean {
        if (instance === this) instance = null
        stopHttp()
        return super.onUnbind(intent)
    }
    override fun onDestroy() {
        if (instance === this) instance = null
        stopHttp()
        super.onDestroy()
    }

    fun snapshot(filter: Boolean): JSONObject {
        val candidates = AccessibilityRootResolver.resolve(this)
        var consumed = 0
        var tree: JSONObject? = null
        val primary = candidates.firstOrNull()?.root
        try {
            val screen = screenBounds()
            val phone = phoneState(primary)
            for (candidate in candidates) {
                consumed++ // Tree builder owns and recycles roots even when building fails.
                val built = AccessibilityTreeBuilder.buildFullAccessibilityTreeJson(
                    candidate.root, if (filter) screen else null,
                ) ?: continue
                if (tree == null) tree = built else tree.getJSONArray("children").put(built)
            }
            return JSONObject().apply {
                put("a11y_tree", tree ?: JSONArray())
                put("phone_state", phone)
                put("device_context", JSONObject().apply {
                    put("screenWidth", screen.width()); put("screenHeight", screen.height())
                    put("sdkInt", Build.VERSION.SDK_INT)
                })
            }
        } finally {
            candidates.drop(consumed).forEach { candidate ->
                runCatching { candidate.root.recycle() }
            }
        }
    }

    private fun screenBounds(): Rect {
        val manager = getSystemService(WINDOW_SERVICE) as WindowManager
        if (Build.VERSION.SDK_INT >= 30) return manager.currentWindowMetrics.bounds
        val size = Point()
        @Suppress("DEPRECATION")
        manager.defaultDisplay.getRealSize(size)
        return Rect(0, 0, size.x, size.y)
    }

    private fun phoneState(root: AccessibilityNodeInfo?): JSONObject {
        val packageName = root?.packageName?.toString()
        val focused = root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        try {
            val allWindows = windows.orEmpty()
            val keyboard = try {
                allWindows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
            } finally {
                allWindows.forEach { runCatching { it.recycle() } }
            }
            return JSONObject().apply {
                put("packageName", packageName ?: JSONObject.NULL)
                put("activityName", if (packageName == lastPackage) lastActivity ?: JSONObject.NULL else JSONObject.NULL)
                put("keyboardVisible", keyboard)
                put("isEditable", focused?.isEditable ?: false)
                put("focusedElement", focused?.viewIdResourceName ?: JSONObject.NULL)
            }
        } finally {
            if (focused !== root) focused?.recycle()
        }
    }
}
