// Jev Bridge; AGPL-3.0-or-later. See LICENSE and NOTICE.md.
package ai.jev.bridge

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import ai.jev.bridge.service.BridgeAccessibilityService

class MainActivity : Activity() {
    private lateinit var status: TextView
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val padding = (24 * resources.displayMetrics.density).toInt()
            setPadding(padding, padding, padding, padding)
        }
        fun label(text: CharSequence, size: Float) = TextView(this).apply {
            this.text = text; textSize = size; setPadding(0, 12, 0, 24)
            layout.addView(this)
        }
        label(getString(R.string.app_name), 30f)
        label(getString(R.string.intro), 16f)
        status = label("", 16f)
        fun button(title: Int, action: () -> Unit) {
            layout.addView(Button(this).apply { setText(title); setOnClickListener { action() } })
        }
        button(R.string.a11y_settings) { open(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        button(R.string.ime_settings) { open(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)) }
        button(R.string.ime_picker) { (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker() }
        button(R.string.source) { open(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/xinwang-nwpu/jev-mobile/tree/main/android/jev-bridge"))) }
        button(R.string.license) {
            val text = assets.open("LICENSE.txt").bufferedReader().use { it.readText() }
            val view = TextView(this).apply { this.text = text; setPadding(24, 16, 24, 16); textSize = 12f }
            AlertDialog.Builder(this).setTitle(R.string.license)
                .setView(ScrollView(this).apply { addView(view) }).setPositiveButton(android.R.string.ok, null).show()
        }
        setContentView(ScrollView(this).apply { addView(layout) })
    }

    override fun onResume() {
        super.onResume()
        val selected = Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
            ?.startsWith("$packageName/") == true
        status.text = getString(R.string.status,
            getString(if (BridgeAccessibilityService.instance != null) R.string.enabled else R.string.disabled),
            getString(if (selected) R.string.selected else R.string.not_selected))
    }
    private fun open(intent: Intent) {
        try { startActivity(intent) }
        catch (_: ActivityNotFoundException) { Toast.makeText(this, R.string.no_handler, Toast.LENGTH_SHORT).show() }
    }
}
