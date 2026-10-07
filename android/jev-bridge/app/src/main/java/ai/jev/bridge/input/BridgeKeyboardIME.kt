// Jev Bridge; AGPL-3.0-or-later. IME session handling adapted from Mobilerun Portal; see NOTICE.md.
package ai.jev.bridge.input

import android.inputmethodservice.InputMethodService
import android.os.SystemClock
import android.util.Base64
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

        internal fun execute(path: String, encoded: String?, clear: Boolean = true, keyCode: Int? = null): String {
            val keyboard = instance ?: error("Jev Bridge Keyboard is not bound to an input field")
            return when (path) {
                "keyboard/input", "keyboard/clear" -> {
                    val clearOnly = path == "keyboard/clear"
                    require(clearOnly || encoded != null) { "base64_text is required" }
                    require((encoded?.length ?: 0) <= 16000) { "Text exceeds the input limit" }
                    val text = if (clearOnly) "" else String(Base64.decode(encoded, Base64.DEFAULT), Charsets.UTF_8)
                    when (val status = keyboard.input(text, clearOnly || clear)) {
                        TextInputResult.Verified -> "verified"
                        TextInputResult.AcceptedUnverified -> "accepted_unverified"
                        else -> error("Text input failed: $status; inspect the field before retrying")
                    }
                }
                "keyboard/key" -> {
                    check(keyboard.key(keyCode ?: error("key_code is required"))) { "Key event was rejected" }
                    "key_sent"
                }
                else -> error("Unknown input endpoint")
            }
        }
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
