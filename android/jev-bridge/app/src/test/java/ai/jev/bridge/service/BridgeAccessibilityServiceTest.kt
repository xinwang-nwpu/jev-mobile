// Jev Bridge; AGPL-3.0-or-later. See LICENSE.
package ai.jev.bridge.service

import android.content.Context
import android.graphics.Point
import android.graphics.Rect
import android.view.Display
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import ai.jev.bridge.core.AccessibilityRootResolver
import ai.jev.bridge.core.AccessibilityTreeBuilder
import io.mockk.*
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Test

class BridgeAccessibilityServiceTest {
    @After fun clean() { unmockkAll() }

    @Test fun snapshotCombinesTreeKeyboardAndScreenAndDelegatesRootOwnership() {
        // Local JVM Android stubs have no Rect implementation.
        mockkConstructor(Rect::class)
        every { anyConstructed<Rect>().width() } returns 1080
        every { anyConstructed<Rect>().height() } returns 2340
        val service = spyk(BridgeAccessibilityService())
        val manager = mockk<WindowManager>()
        val display = mockk<Display>()
        every { service.getSystemService(Context.WINDOW_SERVICE) } returns manager
        every { manager.defaultDisplay } returns display
        every { display.getRealSize(any()) } answers { firstArg<Point>().apply { x = 1080; y = 2340 }; Unit }
        val root = mockk<AccessibilityNodeInfo>(relaxed = true)
        val focus = mockk<AccessibilityNodeInfo>(relaxed = true)
        val keyboard = mockk<AccessibilityWindowInfo>(relaxed = true)
        every { root.packageName } returns "com.example"
        every { root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) } returns focus
        every { focus.isEditable } returns true
        every { focus.viewIdResourceName } returns "com.example:id/input"
        every { service.windows } returns listOf(keyboard)
        every { keyboard.type } returns AccessibilityWindowInfo.TYPE_INPUT_METHOD
        mockkObject(AccessibilityRootResolver, AccessibilityTreeBuilder)
        every { AccessibilityRootResolver.resolve(service) } returns listOf(AccessibilityRootResolver.RootCandidate(root, 1, 0))
        val tree = JSONObject().put("text", "你好").put("children", JSONArray())
        every { AccessibilityTreeBuilder.buildFullAccessibilityTreeJson(root, any()) } returns tree

        val state = service.snapshot(false)
        assertEquals("你好", state.getJSONObject("a11y_tree").getString("text"))
        assertTrue(state.getJSONObject("phone_state").getBoolean("keyboardVisible"))
        assertTrue(state.getJSONObject("phone_state").getBoolean("isEditable"))
        assertEquals("com.example", state.getJSONObject("phone_state").getString("packageName"))
        assertEquals(1080, state.getJSONObject("device_context").getInt("screenWidth"))
        assertEquals(2340, state.getJSONObject("device_context").getInt("screenHeight"))
        verify(exactly = 1) { focus.recycle(); keyboard.recycle() }
        verify(exactly = 0) { root.recycle() } // The tree builder owns this reference.
    }

    @Test fun rootsAreRecycledWhenSnapshotFailsBeforeTraversal() {
        val service = spyk(BridgeAccessibilityService())
        val root = mockk<AccessibilityNodeInfo>(relaxed = true)
        mockkObject(AccessibilityRootResolver)
        every { AccessibilityRootResolver.resolve(service) } returns listOf(AccessibilityRootResolver.RootCandidate(root, 1, 0))
        every { service.getSystemService(Context.WINDOW_SERVICE) } throws IllegalStateException("Display unavailable")
        assertThrows(IllegalStateException::class.java) { service.snapshot(false) }
        verify(exactly = 1) { root.recycle() }
    }
}
