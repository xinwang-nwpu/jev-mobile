# Jev Bridge 的来源与裁剪记录

此 Android App 是 Mobilerun Portal 的裁剪派生版本，采用 **AGPL-3.0-or-later**。
原版权：Copyright (C) 2025 Niels Schmidt。完整上游许可保存在 LICENSE，并随 APK 放入 assets/LICENSE.txt。
Jev Bridge 的修改日期：2026-10-07。修改内容包括包名、界面、服务装配、API 裁剪及树字段裁剪。

上游仓库：https://github.com/droidrun/mobilerun-portal

基线提交：`cc3fa91b15847ca9f7cc9a5afdd6b8e1db7e4ec2`。
本次完整上游检出保存在 jev-mobile 的 `.git/upstream/mobilerun-portal/`，不作为嵌套 Git 仓库提交。
需要再次获取基线时：

```sh
git clone https://github.com/droidrun/mobilerun-portal.git
git -C mobilerun-portal checkout cc3fa91b15847ca9f7cc9a5afdd6b8e1db7e4ec2
```

保留并修改的上游源文件（原路径前缀为 `app/src/main/java/com/mobilerun/portal/`）：

- `core/AccessibilityRootResolver.kt`：多窗口、弹窗及无活动根时的解析；仅替换包名与服务名。
- `core/AccessibilityTraversalGuard.kt`：循环与深度限制；仅替换包名。
- `core/AccessibilityTreeBuilder.kt`：保留遍历、节点回收和可见性逻辑；只输出当前 Python 客户端使用的语义字段，移除 extras、actionList、range/collection 等大块数据。
- `input/InputConnectionTextEditor.kt`：保留安全的整段替换、会话变更处理与提交状态分类；仅替换包名。
- `service/ContentProviderAccessPolicy.kt`：只允许本 App、shell 和 root UID；仅替换包名。

`BridgeAccessibilityService` 的状态组装与 `BridgeKeyboardIME` 的会话管理参考并改编了上游对应部分。
`BridgeContentProvider` 保留 ContentProvider 协议格式与访问策略，删除无关端点后重新组织。
入口界面及构建配置按当前项目需要新建。Gradle Wrapper 来自上游，使用 Gradle 8.13。

对应的树遍历、根解析、输入编辑和 UID 策略测试一并保留；删除了已裁剪 rangeInfo 字段的两条测试。

未迁入：云账号/余额/任务、云回连、HTTP/WebSocket、WebRTC、scrcpy 通道、文件与 APK 管理、短信/联系人/通知触发器、自动更新、自动点击权限弹窗、编号悬浮层、独立保活及 MediaProjection 服务。
这些功能的依赖与权限也未加入。当前截图和点按/滑动继续使用 jev-mobile 的 ADB 通路。

0.1.4 增补：独立实现精简的本机 HTTP 通道，只监听 loopback，使用随机端口与令牌，通过 ADB 转发访问；新增 Android INTERNET 权限。它复用现有树快照和输入编辑器，不迁入上游 HTTP 服务实现。以上“未迁入 HTTP”是初始裁剪记录，WebSocket 等其他项仍未加入。
