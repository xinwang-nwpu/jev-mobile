// Jev Bridge; AGPL-3.0-or-later. IME session handling adapted from Mobilerun Portal; see NOTICE.md.
package ai.jev.bridge.input

import android.inputmethodservice.InputMethodService
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import ai.jev.bridge.R

class BridgeKeyboardIME : InputMethodService() {
    companion object {
        @Volatile var instance: BridgeKeyboardIME? = null
            private set
    }
    @Volatile private var generation = 0L
    private val editor by lazy {
        InputConnectionTextEditor({ currentInputConnection }, { generation }, SystemClock::sleep)
    }

    override fun onCreate() { super.onCreate(); instance = this }
    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }
    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        generation++
        super.onStartInput(attribute, restarting)
    }
    override fun onFinishInput() { generation++; super.onFinishInput() }

    internal fun input(text: String, clear: Boolean): TextInputResult = editor.inputText(text, clear)

    fun key(code: Int): Boolean {
        require(code in 0..KeyEvent.getMaxKeyCode()) { "Invalid key code" }
        val connection = currentInputConnection ?: return false
        val down = connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
        val up = connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
        return down && up
    }

    override fun onCreateInputView(): View = Button(this).apply {
        setText(R.string.switch_keyboard)
        setOnClickListener { (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker() }
    }
}
