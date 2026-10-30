package ai.jev.bridge.core

import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import io.mockk.*
import org.junit.After
import org.junit.Assert.assertThrows
import org.junit.Test

class AccessibilityTextActionsTest {
    private val id = "com.example:id/input"
    @After fun clean() { unmockkAll() }

    private fun rootWith(vararg nodes: AccessibilityNodeInfo): AccessibilityNodeInfo {
        val root = mockk<AccessibilityNodeInfo>(relaxed = true)
        every { root.packageName } returns "com.example"
        every { root.findAccessibilityNodeInfosByViewId(id) } returns nodes.toList()
        return root
    }

    private fun editable(supportsSetText: Boolean = true): AccessibilityNodeInfo {
        val node = mockk<AccessibilityNodeInfo>(relaxed = true)
        val action = mockk<AccessibilityNodeInfo.AccessibilityAction>()
        every { action.id } returns AccessibilityNodeInfo.ACTION_SET_TEXT
        every { node.isEditable } returns true
        every { node.isEnabled } returns true
        every { node.actionList } returns if (supportsSetText) listOf(action) else emptyList()
        return node
    }

    @Test fun sendsTextToUniqueSupportedNode() {
        val node = editable()
        val root = rootWith(node)
        mockkConstructor(Bundle::class)
        every { anyConstructed<Bundle>().putCharSequence(any(), any()) } just Runs
        every { node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, any()) } returns true

        AccessibilityTextActions.setText(root, id, "hello world")

        verify { anyConstructed<Bundle>().putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "hello world") }
        verify(exactly = 1) { node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, any()); node.recycle() }
    }

    @Test fun refusesAmbiguousAndUnsupportedTargetsWithoutExecuting() {
        val first = editable()
        val second = editable()
        assertThrows(IllegalArgumentException::class.java) { AccessibilityTextActions.setText(rootWith(first, second), id, "hello") }
        verify(exactly = 0) { first.performAction(any(), any()); second.performAction(any(), any()) }
        verify(exactly = 1) { first.recycle(); second.recycle() }
        val unsupported = editable(false)
        assertThrows(IllegalArgumentException::class.java) { AccessibilityTextActions.setText(rootWith(unsupported), id, "hello") }
        verify(exactly = 0) { unsupported.performAction(any(), any()) }
    }

    @Test fun refusesWrongAppAndReportsRejectedActions() {
        val node = editable()
        val root = rootWith(node)
        assertThrows(IllegalArgumentException::class.java) { AccessibilityTextActions.setText(root, "other:id/input", "hello") }
        mockkConstructor(Bundle::class)
        every { anyConstructed<Bundle>().putCharSequence(any(), any()) } just Runs
        every { node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, any()) } returns false
        assertThrows(IllegalStateException::class.java) { AccessibilityTextActions.setText(root, id, "hello") }
        verify(exactly = 1) { node.recycle() }
    }
}
