// Jev Bridge; AGPL-3.0-or-later. See LICENSE and NOTICE.md.
package ai.jev.bridge

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import ai.jev.bridge.service.BridgeAccessibilityService

class MainActivity : Activity() {
    private val ink = Color.rgb(25, 48, 42)
    private val muted = Color.rgb(99, 117, 108)
    private val green = Color.rgb(38, 99, 77)
    private val paper = Color.rgb(246, 248, 244)
    private val soft = Color.rgb(232, 240, 230)
    private lateinit var heading: TextView
    private lateinit var summary: TextView
    private lateinit var progress: TextView
    private lateinit var a11yState: TextView
    private lateinit var imeState: TextView
    private lateinit var a11yButton: Button
    private lateinit var imeButton: Button
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val page = column().apply { setPadding(dp(24), dp(20), dp(24), dp(28)) }
        val brand = row()
        brand.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_bridge)
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }, LinearLayout.LayoutParams(dp(42), dp(42)))
        brand.addView(label("Jev Bridge", 21, ink, true).apply { setPadding(dp(12), 0, 0, 0) }, weighted())
        brand.addView(button("关于", soft, ink) { showAbout() }, LinearLayout.LayoutParams(dp(64), dp(48)))
        page.addView(brand)
        val hero = column().apply {
            background = rounded(ink, 28)
            setPadding(dp(20), dp(20), dp(20), dp(20))
        }
        val heroTop = row()
        heroTop.addView(label("设备状态", 12, Color.rgb(166, 193, 177), true), weighted())
        progress = label("0 / 2 项已就绪", 12, Color.rgb(216, 235, 207), true).apply {
            background = rounded(Color.rgb(46, 70, 59), 12)
            setPadding(dp(12), dp(8), dp(12), dp(8))
        }
        heroTop.addView(progress)
        hero.addView(heroTop)
        heading = label("完成连接准备", 26, Color.WHITE, true)
        hero.addView(heading, spaced(12))
        summary = label("", 13, Color.rgb(206, 222, 210))
        hero.addView(summary, spaced(6))
        page.addView(hero, spaced(24))
        page.addView(label("连接设置", 19, ink, true), spaced(20))
        page.addView(label("只需首次设置，返回后自动更新状态。", 13, muted), spaced(6))
        val a11y = setupCard(page, "01", "屏幕读取", "读取当前页面的控件与状态", 16)
        a11yState = a11y.second
        a11yButton = button("开启无障碍服务", green, Color.WHITE) {
            open(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        a11y.first.addView(a11yButton, spaced(12))
        val ime = setupCard(page, "02", "文字输入", "通过 Jev Bridge Keyboard 输入中文", 12)
        imeState = ime.second
        imeButton = button("启用输入法", green, Color.WHITE) {
            val manager = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
            if (manager.enabledInputMethodList.any { it.packageName == packageName }) manager.showInputMethodPicker()
            else open(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
        }
        ime.first.addView(imeButton, spaced(12))
        page.addView(label("通过 ADB 连接电脑后，即可在 jev-mobile 中开始任务。", 13, muted).apply {
            gravity = Gravity.CENTER
        }, spaced(24))
        val container = LinearLayout(this).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            addView(page, LinearLayout.LayoutParams(minOf(resources.displayMetrics.widthPixels, dp(560)), -2))
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(paper); isFillViewport = true; isVerticalScrollBarEnabled = false; addView(container)
        })
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && ::heading.isInitialized) refreshStatus()
    }
    private fun refreshStatus() {
        val connected = BridgeAccessibilityService.instance != null
        val selected = Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
            ?.startsWith("$packageName/") == true
        val enabled = (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager)
            .enabledInputMethodList.any { it.packageName == packageName }
        val count = (if (connected) 1 else 0) + (if (selected) 1 else 0)
        heading.text = if (count == 2) "设备已就绪" else "完成连接准备"
        summary.text = if (count == 2) "可在电脑上运行 jev-mobile。" else "请开启屏幕读取和文字输入。"
        progress.text = "$count / 2 项已就绪"
        a11yState.text = if (connected) "已连接" else "待开启"
        imeState.text = if (selected) "已选中" else if (enabled) "待选择" else "待启用"
        a11yButton.text = if (connected) "管理无障碍服务" else "开启无障碍服务"
        imeButton.text = if (selected) "管理当前输入法" else if (enabled) "选择 Jev Bridge Keyboard" else "启用输入法"
        for ((badge, ready) in listOf(a11yState to connected, imeState to selected)) {
            badge.setTextColor(if (ready) green else muted)
            badge.background = rounded(if (ready) soft else paper, 10)
        }
    }
    private fun setupCard(parent: LinearLayout, number: String, title: String, description: String, gap: Int): Pair<LinearLayout, TextView> {
        val card = column().apply {
            background = rounded(Color.WHITE, 22)
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        val top = row()
        top.addView(label(number, 12, green, true).apply {
            gravity = Gravity.CENTER; background = rounded(soft, 12)
        }, LinearLayout.LayoutParams(dp(36), dp(36)))
        top.addView(label(title, 18, ink, true).apply { setPadding(dp(12), 0, 0, 0) }, weighted())
        val badge = label("", 12, muted, true).apply { setPadding(dp(10), dp(6), dp(10), dp(6)) }
        top.addView(badge)
        card.addView(top)
        card.addView(label(description, 13, muted), spaced(8))
        parent.addView(card, spaced(gap))
        return card to badge
    }
    private fun showAbout() {
        AlertDialog.Builder(this).setTitle("Jev Bridge · ${BuildConfig.VERSION_NAME}")
            .setItems(arrayOf("项目源码", getString(R.string.license))) { _, item ->
                if (item == 0) open(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/xinwang-nwpu/jev-mobile/tree/main/android/jev-bridge")))
                else {
                    val text = assets.open("LICENSE.txt").bufferedReader().use { it.readText() }
                    AlertDialog.Builder(this).setTitle(R.string.license)
                        .setView(ScrollView(this).apply { addView(label(text, 12, ink).apply { setPadding(dp(24), dp(16), dp(24), dp(16)) }) })
                        .setPositiveButton(android.R.string.ok, null).show()
                }
            }.setNegativeButton("关闭", null).show()
    }
    private fun label(value: CharSequence, size: Int, color: Int, bold: Boolean = false) = TextView(this).apply {
        text = value; textSize = size.toFloat(); setTextColor(color)
        if (bold) typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        setLineSpacing(dp(3).toFloat(), 1f)
    }
    private fun button(title: String, color: Int, textColor: Int, action: () -> Unit) = Button(this).apply {
        text = title; isAllCaps = false; textSize = 14f; setTextColor(textColor)
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        minHeight = dp(48); minimumHeight = dp(48)
        setPadding(dp(12), dp(10), dp(12), dp(10))
        background = RippleDrawable(ColorStateList.valueOf(Color.argb(35, 140, 160, 140)), rounded(color, 14), rounded(Color.WHITE, 14))
        elevation = 0f; stateListAnimator = null
        setOnClickListener { action() }
    }
    private fun column() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun row() = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat() }
    private fun spaced(top: Int) = LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(top) }
    private fun weighted() = LinearLayout.LayoutParams(0, -2, 1f)
    private fun dp(value: Int) = (value * resources.displayMetrics.density + 0.5f).toInt()
    private fun open(intent: Intent) {
        try { startActivity(intent) }
        catch (_: ActivityNotFoundException) { Toast.makeText(this, R.string.no_handler, Toast.LENGTH_SHORT).show() }
    }
}
