<h1 align="center">Jev Mobile</h1>

<p align="center">
  用一句话操作 Android 手机。<br>
  让模型看懂当前页面，选择动作，并在需要时交给视觉智能体继续执行。
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#选择模型">选择模型</a> ·
  <a href="#工作原理">工作原理</a> ·
  <a href="#运行记录">运行记录</a> ·
  <a href="android/jev-bridge/README.md">Jev Bridge</a> ·
  <a href="docs/visual-agent.md">视觉智能体</a> ·
  <a href="README.en.md">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Android-8.0%2B-3DDC84?logo=android&logoColor=white" alt="Android 8.0 and later">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9 and later">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/xinwang-nwpu/jev-mobile" alt="License"></a>
</p>

---

```powershell
python -m jev_mobile --task "打开哔哩哔哩，搜索周杰伦的龙卷风"
```

Jev Mobile 读取屏幕和无障碍节点，逐步选择点击、输入、滑动等操作，并通过 ADB 与手机交互。你可以使用云端 Jev、本地多模态 OneJev，或直接使用视觉模型。

<p align="center">
  <img src="docx/demo.gif" width="320" alt="Jev Mobile 在 Android 手机上的操作演示">
  <br><a href="docx/demo.mp4">观看完整演示</a>
</p>

> 演示展示的是项目的一次运行过程；实际速度和结果取决于设备、页面、网络及模型。

## 能做什么

- **根据任务操作手机**：从当前页面选择可操作节点，点击、输入、滚动、返回或等待，动作后重新观察。
- **选择合适的模型**：使用 TypeSafe Jev；使用截图与 A11Y 候选结合的本地 OneJev；或者从第一步使用视觉模型。
- **视觉接管与完成复核**：快模型遇到错误或卡住时可切换视觉执行；OneJev 提议完成后交给视觉模型复核。
- **读取页面并追踪过程**：Jev Bridge 提供 A11Y 树和中文输入；运行记录保存截图、节点、动作、耗时和模型调用。

## 快速开始

以下示例以 Windows PowerShell 和已连接的 Android 手机为例。完整配置项见 [config.example.yaml](config.example.yaml) 和 [.env.example](.env.example)。

### 1. 准备手机与环境

- Python 3.9 或更高版本；安装 [Android platform-tools](https://developer.android.com/tools/releases/platform-tools)，确保 `adb` 可用。
- 手机开启开发者选项和 USB 或无线调试，并在授权提示中允许调试。
- 建议安装 [Jev Bridge](android/jev-bridge/README.md)，启用其无障碍服务和输入法，以读取页面节点并输入中文。未安装时可回退其他支持的通道。

```powershell
adb devices
```

确认设备状态为 `device`。首次使用无线调试时，先按手机提示完成配对和连接。

### 2. 安装项目

```powershell
git clone https://github.com/xinwang-nwpu/jev-mobile.git
cd jev-mobile
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

### 3. 配置模型

编辑 `.env`。例如，使用 TypeSafe Jev 作为快模型时：

```dotenv
FAST_MODEL_BACKEND=jev
FAST_MODEL_URL=https://api.typesafe.ai/v1/systemone
FAST_MODEL_NAME=jev-latest
FAST_MODEL_API_KEY=你的密钥
```

要执行文字输入，还需要配置 `TEXT_MODEL_API_KEY`、`TEXT_MODEL_BASE_URL` 和 `TEXT_MODEL`。要启用视觉接管或完成复核，需要配置 `VISION_MODEL_API_KEY`、`VISION_MODEL_BASE_URL` 和支持图片输入的 `VISION_MODEL`。这些服务须提供项目使用的 OpenAI 兼容 Chat Completions 接口。

使用本地 OneJev 或纯视觉模式，请看[选择模型](#选择模型)。密钥放在本机 `.env`，不要提交到仓库。

### 4. 连接设备并运行

在 `config.yaml` 中设置 `adb_path`；只有一台设备时 `device` 可留空，多台设备时填写 `adb devices` 显示的序列号。运行：

```powershell
python -m jev_mobile --task "打开设置"
```

程序会输出每一步的决策、动作及耗时，并默认将轨迹保存到 `runs/<任务名>/`。退出状态为 `done` 表示执行流程判定任务完成；可以通过保存的截图和记录检查结果。

## 选择模型

| 方式             | 输入与执行                                                 | 需要准备                                                        |
| ---------------- | ---------------------------------------------------------- | --------------------------------------------------------------- |
| **TypeSafe Jev** | 根据 A11Y 元素选择操作和目标；快路径包含独立完成判定       | TypeSafe 接口和密钥；输入文字时需要文本模型                     |
| **OneJev**       | 同时接收截图和 A11Y 动作候选，联合选择操作及目标           | 本地 SystemOne 服务；输入文字需要文本模型；完成复核需要视觉模型 |
| **纯视觉**       | 从第一步使用截图和可用的 A11Y 编号，由视觉智能体执行和复核 | 支持图片输入和 JSON 输出的视觉模型                              |

### 使用本地 OneJev

先自行部署并启动支持 SystemOne 接口的 OneJev 服务。模型文件和推理服务不包含在本仓库中。随后在 `.env` 中设置：

```dotenv
FAST_MODEL_BACKEND=onejev
FAST_MODEL_URL=http://127.0.0.1:8000/v1/systemone
FAST_MODEL_NAME=OneJev1.6-4B-Q4_K_M
FAST_MODEL_API_KEY=
```

在 `config.yaml` 中设置 `vision_only: false`、`vision_fallback: true`，并配置视觉模型。OneJev 每轮需要截图，不支持 `--no-screenshots`。它不会额外请求任务完成概率；选择 `DONE` 后由视觉智能体接管并复核。

### 直接使用视觉模型

配置 `VISION_MODEL_*` 后运行：

```powershell
python -m jev_mobile --vision-only --task "打开设置"
```

也可以在 `config.yaml` 设置 `vision_only: true`。`vision_mode: fast` 跳过 Planner，由 Executor 逐步执行并在完成时复核；`planned` 使用 Planner、Executor 和 Verifier。角色和提示词详见 [视觉智能体说明](docs/visual-agent.md)。

## 工作原理

```text
用户任务
   ↓
观察：截图 / A11Y 树 → 筛选可操作节点 → 分配本轮临时编号
   ↓
决策：Jev 或 OneJev 选择动作和目标
   ↓
执行：ADB 点击、滑动等；Jev Bridge 或其他输入通道输入文字
   ↓
等待 → 重新观察 → 下一步
   ↓ 需要恢复或复核时
视觉智能体接管 → 执行 / 完成复核
```

快路径支持点击、输入、滚动、返回、主页、回车和等待。视觉路径还支持按坐标或本轮 A11Y 编号执行点击、长按、输入、滑动和启动应用。编号随每次观察重新生成；执行器只执行预定义动作。

截图可叠加坐标网格和 A11Y 编号框，在 `config.yaml` 中通过 `vision_a11y_boxes` 控制。截图和 A11Y 树是独立读取的：页面正在切换时，两者可能不完全同步；发送消息等操作应检查实际目标和执行结果。

## 运行记录

默认目录为 `runs/<任务名>/`，可用 `--record-dir` 指定其他位置：

| 文件          | 内容                                              |
| ------------- | ------------------------------------------------- |
| `trace.json`  | 动作、目标、执行结果、模型调用、切换原因及耗时    |
| `*.a11y.json` | 当轮进入操作表的节点属性、`actionList` 和重复标记 |
| 页面截图      | 视觉路径及启用截图时的页面图片                    |

动作记录中的 `target_node` 保存目标节点的完整资源 ID、类名、原始文字与描述、原生边界，以及点击、编辑、启用、勾选、选中和焦点状态。坐标操作没有关联节点时，该字段为空。

重复检测只比较可见、启用、边界有效的节点；`resourceId`、`className`、`text`、`contentDescription` 四项完全相同时标为重复。单独采集当前页面并生成重复标注和截图：

```powershell
python script/capture_a11y.py --device 设备序列号
```

采集脚本的红框表示重复节点、绿框表示唯一节点；智能体的彩色编号框用于操作定位，两者用途不同。

## 常用配置

```yaml
vision_only: false       # true：从第一步使用视觉模型
vision_fallback: true    # 快路径出错或卡住时允许视觉接管
vision_mode: fast        # fast 或 planned
vision_a11y_boxes: true  # 截图上显示可操作节点编号框
```

更多选项包括 `--device`、`--adb-path`、`--record-dir`、`--start-package`、`--no-vision-fallback` 和 `--quiet`。查看完整参数：

```powershell
python -m jev_mobile --help
```

如果 `adb devices` 显示 `unauthorized`，请在手机上确认调试授权；如果中文输入失败，检查 Jev Bridge 输入法是否已启用并选中。设备状态、安装和接口说明见 [Jev Bridge README](android/jev-bridge/README.md)。

## 项目结构

```text
jev_mobile/           智能体循环、模型请求、设备通信与 A11Y 处理
android/jev-bridge/   Android 辅助 App
script/               页面树采集与标注脚本
tests/                Python 测试
docs/                 视觉实现、性能分析和研究笔记
```
