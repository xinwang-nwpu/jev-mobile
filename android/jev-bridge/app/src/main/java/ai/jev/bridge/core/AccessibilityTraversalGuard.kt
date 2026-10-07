// Derived from droidrun/mobilerun-portal; AGPL-3.0-or-later. See NOTICE.md.
package ai.jev.bridge.core

import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo

object AccessibilityTraversalGuard {
    const val MAX_ACCESSIBILITY_TREE_DEPTH = 128

    fun isTooDeep(depth: Int): Boolean = depth > MAX_ACCESSIBILITY_TREE_DEPTH

    fun enterActivePath(
        node: AccessibilityNodeInfo,
        activeNodePath: MutableSet<AccessibilityNodeInfo>
    ): Boolean {
        return activeNodePath.add(node)
    }

    fun leaveActivePath(
        node: AccessibilityNodeInfo,
        activeNodePath: MutableSet<AccessibilityNodeInfo>
    ) {
        activeNodePath.remove(node)
    }

    fun isActiveNodeReference(
        node: AccessibilityNodeInfo,
        activeNodePath: Set<AccessibilityNodeInfo>
    ): Boolean {
        return activeNodePath.any { activeNode -> activeNode === node }
    }

    fun createTraversalKey(node: AccessibilityNodeInfo, rect: Rect): String {
        return listOf(
            safeInt { node.windowId }?.toString().orEmpty(),
            "${rect.left},${rect.top},${rect.right},${rect.bottom}",
            safeString { node.className },
            safeString { node.viewIdResourceName },
            safeString { node.packageName },
            uniqueId(node),
        ).joinToString(separator = "|")
    }

    private fun uniqueId(node: AccessibilityNodeInfo): String {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            safeString { node.uniqueId }
        } else {
            ""
        }
    }

    private inline fun safeString(block: () -> CharSequence?): String {
        return try {
            block()?.toString().orEmpty()
        } catch (_: RuntimeException) {
            ""
        }
    }

    private inline fun safeInt(block: () -> Int): Int? {
        return try {
            block()
        } catch (_: RuntimeException) {
            null
        }
    }
}
