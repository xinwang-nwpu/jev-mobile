// Derived from droidrun/mobilerun-portal; AGPL-3.0-or-later. See NOTICE.md.
package ai.jev.bridge.core

import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Utility class for building comprehensive JSON representations of accessibility trees
 */
object AccessibilityTreeBuilder {
    private const val TAG = "AccessibilityTreeBuilder"

    private const val VISIBILITY_THRESHOLD = 0.01f  // 1% visibility threshold

    /**
     * Builds a comprehensive JSON object from an AccessibilityNodeInfo node,
     * extracting all available properties and recursively processing children.
     * Optionally filters out nodes that are less than 1% visible on screen.
     *
     * @param node The AccessibilityNodeInfo to convert to JSON
     * @param screenBounds The visible screen bounds for filtering (null to disable filtering)
     * @return JSONObject containing all extractable node information, or null if filtered out
     */
    fun buildFullAccessibilityTreeJson(
        node: AccessibilityNodeInfo,
        screenBounds: Rect? = null
    ): JSONObject? {
        return buildFullAccessibilityTreeJson(node, screenBounds, 0, mutableSetOf())
    }

    private fun buildFullAccessibilityTreeJson(
        node: AccessibilityNodeInfo,
        screenBounds: Rect?,
        depth: Int,
        activeNodePath: MutableSet<AccessibilityNodeInfo>
    ): JSONObject? {
        val rect = Rect()
        try {
            node.getBoundsInScreen(rect)
        } catch (e: RuntimeException) {
            Log.e(TAG, "Unable to read accessibility node bounds: ${e.message}", e)
            node.recycle()
            return null
        }

        val nodeKey = AccessibilityTraversalGuard.createTraversalKey(node, rect)
        if (AccessibilityTraversalGuard.isTooDeep(depth)) {
            Log.w(
                TAG,
                "Skipping accessibility subtree deeper than " +
                    "${AccessibilityTraversalGuard.MAX_ACCESSIBILITY_TREE_DEPTH} levels: $nodeKey",
            )
            node.recycle()
            return null
        }
        if (!AccessibilityTraversalGuard.enterActivePath(node, activeNodePath)) {
            Log.w(
                TAG,
                "Skipping cyclic accessibility node while building full tree: $nodeKey",
            )
            if (!AccessibilityTraversalGuard.isActiveNodeReference(node, activeNodePath)) {
                node.recycle()
            }
            return null
        }

        try {
            // Check this node's validity (only if filtering is enabled)
            val nodePassesFilter = if (screenBounds != null) {
                val visiblePercentage = getVisiblePercentage(rect, screenBounds)
                visiblePercentage >= VISIBILITY_THRESHOLD
            } else {
                true  // No filtering, always passes
            }

            // Process children FIRST (before deciding on this node)
            val childrenArray = JSONArray()
            val childCount = try {
                node.childCount
            } catch (e: RuntimeException) {
                Log.e(TAG, "Unable to read child count for accessibility node $nodeKey: ${e.message}", e)
                0
            }
            for (i in 0 until childCount) {
                val child = try {
                    node.getChild(i)
                } catch (e: RuntimeException) {
                    Log.e(
                        TAG,
                        "Unable to read child accessibility node index=$i parent=$nodeKey: ${e.message}",
                        e,
                    )
                    null
                } ?: continue

                if (child === node) {
                    Log.w(TAG, "Skipping child accessibility node that references its parent: $nodeKey")
                    continue
                }

                if (AccessibilityTraversalGuard.isActiveNodeReference(child, activeNodePath)) {
                    Log.w(TAG, "Skipping child accessibility node that references an active ancestor: $nodeKey")
                    continue
                }

                val childJson = buildFullAccessibilityTreeJson(
                    child,
                    screenBounds,
                    depth + 1,
                    activeNodePath,
                )
                if (childJson != null) {
                    childrenArray.put(childJson)
                }
            }

            // Parent preservation: keep if passes filter OR has valid children
            if (!nodePassesFilter && childrenArray.length() == 0) {
                return null
            }

            // Keep only the semantic fields consumed by jev-mobile.
            return JSONObject().apply {
                put("resourceId", node.viewIdResourceName ?: "")
                put("className", node.className?.toString() ?: "")
                put("packageName", node.packageName?.toString() ?: "")
                put("text", node.text?.toString() ?: "")
                put("contentDescription", node.contentDescription?.toString() ?: "")
                put("boundsInScreen", JSONObject().apply {
                    put("left", rect.left); put("top", rect.top)
                    put("right", rect.right); put("bottom", rect.bottom)
                })
                put("isClickable", node.isClickable)
                put("isLongClickable", node.isLongClickable)
                put("isEnabled", node.isEnabled)
                put("isVisibleToUser", node.isVisibleToUser)
                put("isEditable", node.isEditable)
                put("isPassword", node.isPassword)
                put("isScrollable", node.isScrollable)
                put("isChecked", node.isChecked)
                put("isSelected", node.isSelected)
                put("isFocused", node.isFocused)
                put("children", childrenArray)
            }
        } catch (e: RuntimeException) {
            Log.e(TAG, "Unable to build full accessibility tree node $nodeKey: ${e.message}", e)
            return null
        } finally {
            AccessibilityTraversalGuard.leaveActivePath(node, activeNodePath)
            node.recycle()
        }
    }

    private fun getVisiblePercentage(rect: Rect, screenBounds: Rect): Float {
        val width = rect.right - rect.left
        val height = rect.bottom - rect.top
        val totalArea = width * height

        if (totalArea <= 0) return 0f

        // Check if element fully contains screen (overflow case)
        if (rect.left <= 0 && rect.top <= 0 &&
            rect.right >= screenBounds.right && rect.bottom >= screenBounds.bottom) {
            return 1f
        }

        // Calculate visible portion
        val visibleLeft = maxOf(rect.left, screenBounds.left)
        val visibleTop = maxOf(rect.top, screenBounds.top)
        val visibleRight = minOf(rect.right, screenBounds.right)
        val visibleBottom = minOf(rect.bottom, screenBounds.bottom)

        val visibleWidth = maxOf(0, visibleRight - visibleLeft)
        val visibleHeight = maxOf(0, visibleBottom - visibleTop)
        val visibleArea = visibleWidth * visibleHeight

        return visibleArea.toFloat() / totalArea.toFloat()
    }
}
