# Jev Bridge

`ai.jev.bridge` 是 jev-mobile 的独立 Android 辅助 App，Android 8.0+。源码和 APK 均为 AGPL-3.0-or-later 的派生作品；来源、基线和裁剪明细见 [NOTICE.md](NOTICE.md)。

保留屏幕控件读取、手机状态及中文输入。HTTP 服务仅监听手机本机，经 ADB 转发访问；没有云登录、文件管理、短信或联系人读取权限。截图、点击、长按、滑动仍由 Python 客户端通过 ADB 完成。它不是一个新的决策模型。

## 构建与安装

需要 JDK 17+、Android SDK 34 和联网下载 Gradle 依赖。在 Android Studio 打开本目录，或设置 `ANDROID_HOME` 后运行：

```powershell
.\gradlew.bat :app:assembleDebug :app:testDebugUnitTest
adb install -r .\app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n ai.jev.bridge/.MainActivity
```

打开 App，通过“屏幕读取”和“文字输入”两张设置卡片启用无障碍服务、启用并选择“Jev Bridge Keyboard”。返回页面后状态自动刷新，两项都就绪时顶部显示“设备已就绪”。选中后，“管理当前输入法”可切换回日常键盘。新版包名独立，可与原 Portal 同时安装。源码和许可入口位于右上角“关于”，主页面只展示连接设置。

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
adb shell content insert --uri content://ai.jev.bridge/keyboard/clear --bind clear:b:true
adb shell content insert --uri content://ai.jev.bridge/keyboard/key --bind key_code:i:66
```

`state` 与 `state_full` 同样返回精简完整树；`a11y_tree` 与 `a11y_tree_full` 只返回树。`state_full` 的结构为 `{a11y_tree, phone_state, device_context}`。节点保留 text、contentDescription、resourceId、className、packageName、boundsInScreen、交互状态和 children。多窗口根仍合并，循环与最大深度仍限制；节点由树构建器回收。

中文输入必须已经选中此输入法，并且目标输入框具有 InputConnection。输入成功返回含 `status=success` 的 URI，message 区分 `verified` 和 `accepted_unverified`；后者只表示输入法接受了提交，仍需 Python 的动作后观察确认。提交状态未知、会话变化或拒绝会抛出错误，不能自动当成“没输入过”重复提交。

`content insert` 需要至少一个 `--bind` 参数，因此清空示例也传入 `clear:b:true`。部分系统不会打印 insert 返回的 URI；命令退出成功后，仍应通过 `state_full` 的输入框文字或截图确认实际效果。空输入框的提示文字作为控件标签保留，不计入已输入内容。

### HTTP 加速（0.1.4+）

Python `Device` 默认自动接入，无需修改配置。首次通过受 UID 保护的 `content://ai.jev.bridge/http_info` 读取 `{port, token, protocol}`；此时按需启动服务，监听 `127.0.0.1` 的随机端口。客户端创建自己的 `adb forward tcp:0 tcp:<port>`，使用标准库 `http.client` 直接访问本机转发端口，不加载 HTTPS 证书或代理配置。

所有 HTTP 请求必须带 `Authorization: Bearer <token>`；令牌随机生成，随服务销毁失效，不写日志。支持 `GET /ping`、`GET /state_full`（可加 `?filter=false`），以及 `POST /keyboard/input`、`/keyboard/clear`、`/keyboard/key`。POST 为 JSON，字段与 content 接口对应：`base64_text`、布尔 `clear`、整数 `key_code`。HTTP 与 content 复用相同的树快照和输入编辑器；截图保持 ADB 通道。关闭无障碍服务时 HTTP 服务随之关闭。

旧 APK、读取失败、连接建立失败、认证或路由拒绝时自动回退 content；本次运行停止反复探测故障 HTTP。输入请求一旦发出后超时、连接中断、结果损坏或编辑器报错，不能确定是否已经写入，此时明确报错要求观察输入框，不自动重复提交。动作后观察与完成复核仍保留。`Device.close()` 清理自己创建的转发并恢复原输入法，不影响其他客户端的转发。

## 二次开发入口

| 文件 | 作用 |
| --- | --- |
| `service/BridgeContentProvider.kt` | ADB 接口与调用方校验 |
| `service/BridgeHttpServer.kt` | 本机 HTTP、令牌认证、请求限制 |
| `service/BridgeAccessibilityService.kt` | 服务生命周期、手机状态、树快照 |
| `core/AccessibilityTreeBuilder.kt` | 保留哪些节点字段 |
| `core/AccessibilityRootResolver.kt` | 当前窗口和弹窗根选择 |
| `input/BridgeKeyboardIME.kt` | 输入法生命周期与操作 |
| `input/InputConnectionTextEditor.kt` | 原子替换、提交状态与输入会话处理 |
| `MainActivity.kt` | 权限入口、状态、源码和许可页面 |

路径以 `app/src/main/java/ai/jev/bridge/` 为前缀。修改协议时保持 Python 的 `normalize_tree()`、`Device._portal_tree()` 和输入路径同步。单纯更改 UI 不需要调整客户端。
