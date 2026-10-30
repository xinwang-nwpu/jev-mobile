# jev-mobile

Android 手机自动化项目：使用 A11Y 节点和模型选择操作，通过 ADB 执行；支持 TypeSafe Jev、本地多模态 OneJev，以及视觉智能体接管和完成复核。

[English](README.en.md) · [Jev Bridge](android/jev-bridge/README.md) · [视觉智能体设计](docs/visual-agent.md)

## 运行流程

```text
任务 + 当前页面
   ↓
Jev / OneJev 选择动作和目标
   ↓
ADB 或 Jev Bridge 执行 → 重新观察 → 下一步
   ↓ 遇到问题或 OneJev 提议完成
视觉智能体接管 → 执行或复核 → 结果
```

快路径从 A11Y 树生成本轮可操作节点表。Jev 使用操作、目标和完成判定；OneJev 接收截图和动作候选，在一次选择中决定操作和目标，完成提议交给视觉智能体复核。快路径输入文字由 `TEXT_MODEL_*` 指定的文本模型生成。

视觉智能体支持截图和 A11Y 编号，提供 planned（规划、执行、复核）与 fast（执行、复核）两种流程。`vision_only: true` 从第一步使用视觉智能体；配置视觉模型并启用 `vision_fallback` 时，快路径遇到模型错误、无目标、执行错误或卡住等情况可以切换到视觉路径。完成复核依据观察和执行记录判断，不保证模型判断始终正确。

Jev Bridge 是项目的 Android 辅助 App，负责获取无障碍树、手机状态和中文输入。Python 端优先通过 ADB 转发的本机 HTTP 通道通信，失败时回退 ContentProvider；也支持兼容 Portal 和 `uiautomator`。截图、点击、滑动等由 ADB 执行。Bridge 还提供直接调用 `ACTION_SET_TEXT` 的独立接口，详见 [Bridge 接口说明](android/jev-bridge/README.md)。

## 快速开始

需要 Python 3.9+、[Android platform-tools](https://developer.android.com/tools/releases/platform-tools)、已开启 USB 或无线调试的 Android 设备。建议安装并启用 [Jev Bridge](android/jev-bridge/README.md) 的无障碍服务和输入法，以读取节点和输入中文。

```powershell
pip install -e .
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
adb devices
python -m jev_mobile --task "打开设置"
```

在 `.env` 中配置快模型、文本模型和视觉模型；在 `config.yaml` 中配置设备、任务、等待时间和视觉模式。真实密钥保存在本地 `.env`，不要提交到 Git。

### 快模型配置

本地 OneJev 需要单独部署并启动支持 SystemOne 接口的服务；模型文件和服务不包含在本仓库中：

```dotenv
FAST_MODEL_BACKEND=onejev
FAST_MODEL_URL=http://127.0.0.1:8000/v1/systemone
FAST_MODEL_NAME=OneJev1.6-4B-Q4_K_M
FAST_MODEL_API_KEY=
```

使用 TypeSafe Jev 时：

```dotenv
FAST_MODEL_BACKEND=jev
FAST_MODEL_URL=https://api.typesafe.ai/v1/systemone
FAST_MODEL_NAME=jev-latest
FAST_MODEL_API_KEY=你的密钥
```

旧版 `TYPESAFE_API_KEY` 和 `TYPESAFE_MODEL` 在对应新变量为空时仍可读取。OneJev 必须使用截图，不能传 `--no-screenshots`；单次动作候选超过 255 项或请求失败时，配置了视觉接管则交给视觉智能体。

### 文本和视觉模型

需要输入文字时配置支持 OpenAI 兼容 Chat Completions 的文本模型；视觉接管和完成复核需要支持图片输入的视觉模型：

```dotenv
TEXT_MODEL_API_KEY=你的密钥
TEXT_MODEL_BASE_URL=https://你的服务/v1
TEXT_MODEL=你的文本模型
VISION_MODEL_API_KEY=你的密钥
VISION_MODEL_BASE_URL=https://你的服务/v1
VISION_MODEL=你的视觉模型
```

Planner、Executor 和 Verifier 默认使用 `VISION_MODEL`，可分别使用 `VISION_PLANNER_MODEL`、`VISION_EXECUTOR_MODEL`、`VISION_VERIFIER_MODEL` 覆盖；其他选项见 [.env.example](.env.example)。

### 运行配置

```yaml
task: "打开设置"
adb_path: 'C:\platform-tools\adb.exe'
device: null                 # 多设备时填写 adb serial
vision_only: false           # true：只使用视觉智能体
vision_fallback: true        # 快路径遇到问题时允许视觉接管
vision_mode: fast            # fast 或 planned
vision_a11y_boxes: true      # 给截图叠加节点框和临时编号
action_interval: 1.0         # 每次动作后等待秒数，再观察页面；0 为不额外等待
```

`action_interval` 是固定等待，不保证页面已经稳定。截图与 A11Y 是独立读取，同一 Activity 内布局变化可能造成截图、节点位置和执行坐标不一致；对发送消息等操作，应留意目标页面和执行结果。

## 动作和观察

快路径支持 `CLICK`、`TYPE_TEXT`、滚动、返回、主页、回车、等待、完成和阻塞。视觉路径支持按 A11Y 编号或坐标点击、长按、输入、滑动、启动 App、返回、主页、回车和等待。编号只对当前观察有效；模型输出不会作为 shell 命令执行。

动作完成后重新观察。连续无变化或反复切换页面时，快路径可以转入视觉恢复。视觉 planned 模式对编号目标执行前检查当前窗口和节点；视觉 fast 跳过执行前复读。OneJev 的完成提议需要配置视觉模型复核；TypeSafe Jev 保留独立的目标完成判定。

每次运行默认在 `runs/<任务名>/` 保存轨迹和页面数据：

- `trace.json`：动作、目标、结果、模式切换、模型用量和阶段耗时。
- `*.a11y.json`：进入当前操作表的节点属性和重复标记；保留节点的 `actionList`，以平铺形式保存。
- 截图：视觉路径和启用截图的快路径保存的页面图像。

`history[].target_node` 保存动作前目标的 resource ID、完整类名、文字、无障碍描述、原生屏幕边界及点击、编辑、启用、勾选、选中、焦点状态。纯坐标或无节点的操作该字段为空。重复检测只对可见、启用且边界有效的节点进行，以 `resourceId`、`className`、`text`、`contentDescription` 四项完全相同为判据。

单独读取当前页面、导出整棵树的重复标注及带框截图：

```powershell
python script/capture_a11y.py --device 设备序列号
```

脚本的红框表示重复节点，绿框表示唯一节点；智能体的彩色编号框由 `vision_a11y_boxes` 控制，两者用途不同。

## 命令行

```powershell
python -m jev_mobile --task "打开设置" --device 设备序列号
python -m jev_mobile --vision-only --task "打开设置"
python -m jev_mobile --action-interval 1.5 --task "打开设置"
```

命令行可覆盖任务、ADB 路径、设备、起始包名、记录目录、截图和动作等待时间；`--no-vision-fallback` 关闭视觉接管，`--quiet` 精简日志。完整参数可通过 `python -m jev_mobile --help` 查看。

## 代码结构

| 位置 | 内容 |
| --- | --- |
| `jev_mobile/agent.py` | 动作循环、快慢切换、执行记录和预算 |
| `jev_mobile/model.py` | Jev、OneJev 选择请求及文本助手 |
| `jev_mobile/device.py` | ADB、Bridge 通信、观察与执行 |
| `jev_mobile/a11y.py`、`selectors.py` | 节点解析、动作候选、重复检测 |
| `jev_mobile/vision.py`、`visual_prompts.py`、`visual_images.py` | 视觉规划、执行、复核、图片处理 |
| `android/jev-bridge/` | Android 辅助 App 和接口 |
| `script/capture_a11y.py` | 读取 A11Y 树并生成带框截图 |
| `docs/` | 视觉实现、性能分析与研究笔记 |

## 开发与许可

```powershell
python -m pytest
```

Android App 的构建和测试命令见 [Jev Bridge README](android/jev-bridge/README.md)。项目许可见 [LICENSE](LICENSE)，Jev Bridge 的来源和许可见 [NOTICE](android/jev-bridge/NOTICE.md)。
