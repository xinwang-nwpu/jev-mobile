# Jev Bridge

`ai.jev.bridge` 是 jev-mobile 的独立 Android 辅助 App，Android 8.0+。源码和 APK 均为 AGPL-3.0-or-later 的派生作品；来源、基线和裁剪明细见 [NOTICE.md](NOTICE.md)。

保留屏幕控件读取、手机状态及中文输入。没有网络服务、云登录、文件管理、短信或联系人读取权限。截图、点击、长按、滑动仍由 Python 客户端通过 ADB 完成。它不是一个新的决策模型。

## 构建与安装

需要 JDK 17+、Android SDK 34 和联网下载 Gradle 依赖。在 Android Studio 打开本目录，或设置 `ANDROID_HOME` 后运行：

```powershell
.\gradlew.bat :app:assembleDebug :app:testDebugUnitTest
adb install -r .\app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n ai.jev.bridge/.MainActivity
```

打开 App，先启用“Jev Bridge”无障碍服务，再启用并选择“Jev Bridge Keyboard”。输入法在自动化期间占用系统输入位置，底部“切换输入法”按钮可切回日常键盘。新版包名独立，可与原 Portal 同时安装。

部分 Android 系统对侧载 App 的无障碍服务显示“受限设置”；需在系统的 App 详情菜单允许受限设置后才能开启。无障碍与输入法的用户授权不会由 App 静默绕过。

开发 APK 使用本机 Android debug 签名。后续更新应保持签名一致；正式发布时再配置自己的 release 签名。密钥与本机 SDK 路径不提交到 Git。

## 本地接口

接口只允许本 App、ADB shell、root；其他 App 调用会被拒绝。所有 query 返回一列 `result`，内容为 JSON：`{"status":"success","result":...}`，失败为 `{"status":"error","message":...}`。

```powershell
adb shell content query --uri content://ai.jev.bridge/ping
adb shell content query --uri content://ai.jev.bridge/state_full
adb shell content query --uri "content://ai.jev.bridge/state_full?filter=false"
adb shell content query --uri content://ai.jev.bridge/phone_state
adb shell content query --uri content://ai.jev.bridge/packages
adb shell content query --uri content://ai.jev.bridge/version
# UTF-8 的“你好”经过 Base64 编码；默认 clear=true 替换整个输入框。
adb shell content insert --uri content://ai.jev.bridge/keyboard/input --bind base64_text:s:5L2g5aW9
adb shell content insert --uri content://ai.jev.bridge/keyboard/clear
adb shell content insert --uri content://ai.jev.bridge/keyboard/key --bind key_code:i:66
```

`state` 与 `state_full` 同样返回精简完整树；`a11y_tree` 与 `a11y_tree_full` 只返回树。`state_full` 的结构为 `{a11y_tree, phone_state, device_context}`。节点保留 text、contentDescription、resourceId、className、packageName、boundsInScreen、交互状态和 children。多窗口根仍合并，循环与最大深度仍限制；节点由树构建器回收。

中文输入必须已经选中此输入法，并且目标输入框具有 InputConnection。输入成功返回含 `status=success` 的 URI，message 区分 `verified` 和 `accepted_unverified`；后者只表示输入法接受了提交，仍需 Python 的动作后观察确认。提交状态未知、会话变化或拒绝会抛出错误，不能自动当成“没输入过”重复提交。

## 二次开发入口

| 文件 | 作用 |
| --- | --- |
| `service/BridgeContentProvider.kt` | ADB 接口与调用方校验 |
| `service/BridgeAccessibilityService.kt` | 服务生命周期、手机状态、树快照 |
| `core/AccessibilityTreeBuilder.kt` | 保留哪些节点字段 |
| `core/AccessibilityRootResolver.kt` | 当前窗口和弹窗根选择 |
| `input/BridgeKeyboardIME.kt` | 输入法生命周期与操作 |
| `input/InputConnectionTextEditor.kt` | 原子替换、提交状态与输入会话处理 |
| `MainActivity.kt` | 权限入口、状态、源码和许可页面 |

路径以 `app/src/main/java/ai/jev/bridge/` 为前缀。修改协议时保持 Python 的 `normalize_tree()`、`Device._portal_tree()` 和输入路径同步。单纯更改 UI 不需要调整客户端。
