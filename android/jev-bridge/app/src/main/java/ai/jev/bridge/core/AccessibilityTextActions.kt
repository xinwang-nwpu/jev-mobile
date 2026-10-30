package ai.jev.bridge.core

import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo

internal object AccessibilityTextActions {
    fun setText(root: AccessibilityNodeInfo, resourceId: String, text: String) {
        require(resourceId.isNotBlank() && resourceId.contains(":id/")) { "A full resource_id is required" }
        require(resourceId.substringBefore(":id/") == root.packageName?.toString()) { "Resource ID does not belong to the active app" }
        val matches = root.findAccessibilityNodeInfosByViewId(resourceId).orEmpty()
        try {
            val targets = matches.filter { it.isEditable && it.isEnabled }
            require(targets.size == 1) { "Expected exactly one enabled editable node; found ${targets.size}" }
            val target = targets.single()
            require(target.actionList.any { it.id == AccessibilityNodeInfo.ACTION_SET_TEXT }) { "Node does not support SET_TEXT" }
            val arguments = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            check(target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)) { "SET_TEXT was rejected" }
        } finally {
            matches.forEach { runCatching { it.recycle() } }
        }
    }
}
