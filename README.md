# jev-mobile ⚡

**Android 手机自动化 agent：一次 Jev 模型请求同时决策"做什么操作"和"操作哪个元素"，无截图、纯 A11Y 无障碍树结构化状态，ADB 直接执行。**

[English](README.en.md)

给它一句自然语言目标，例如"打开设置，把飞行模式开关打开"。[TypeSafe Jev](https://docs.typesafe.ai/) 从 A11Y 元素表里选出操作和目标元素；只有当操作是 `TYPE_TEXT` 时，才由一个小 LLM 生成要输入的文本。整个过程不依赖截图识别，决策快、执行快。

## 演示
针对任务"在哔哩哔哩，播放龙卷风视频"，总共用时18秒每次动作决策只需要2-3s!，完全不需要多模态大模型参与token消耗量极小。
<a href="docx/demo.mp4"><img src="docx/demo.gif" alt="真机演示：一句自然语言目标，Jev 每步一次请求完成决策与执行" width="320" /></a>

[观看 MP4](docx/demo.mp4)

## 动作空间

每次观察（一次 A11Y 树快照）产生一个新的索引化元素表：

```text
[1] EditText  query        · value=""
[2] TextView  Go
...

操作：CLICK、TYPE_TEXT、SCROLL_UP、SCROLL_DOWN、BACK、HOME、ENTER、WAIT、DONE、BLOCKED
```

```text
                      一次 TypeSafe 请求
                     ┌───────────────────────────┐
A11Y树 → 元素表 ────→ │ operation（选哪个操作）      │
                     │ click_target（点谁）         │
                     │ type_text_target（往哪输）    │
                     └─────────────┬─────────────┘
                       只消费与所选操作匹配的目标头
                                   │
                    CLICK [2] ────┤──→ adb input tap
                TYPE_TEXT [1] ────┘
                          ↓
                   小 LLM → 文本 → adb 键盘
```

目标问题都是投机的：若操作是 `CLICK`，只有 `click_target` 会被执行。两个决策、**一次网络往返**；每个目标头只包含与该操作兼容的元素。元素索引由代码分配（快照内的位置），模型输出永远只是"已观察到的索引"——不会变成选择器、坐标或 shell 命令，也就不存在被 UI 文本注入操纵的执行路径。

## 为什么快

- **每步只发一次模型请求。** 操作头和所有目标头共享同一份观察状态，在同一次请求里并行求值。
- **默认循环不截图。** Jev 消费结构化状态：className、text、contentDescription、resourceId、bounds、checked/selected 等语义属性。
- **一次 A11Y 读取覆盖全部状态，且三路并行。** 优先读 Portal（`com.mobilerun.portal` / `com.droidrun.portal` 的 ContentProvider，一条 `content query` 拿到整棵树和手机状态），未安装时回退 `uiautomator dump /dev/tty`（单次往返直接取回 XML）。树、焦点窗口、截图并发执行，一次观察只花最慢一路的时间。
- **廉价的新鲜度守卫。** 预测前比较 `dumpsys window` 焦点窗口是否变化（设备端 grep，只传回一行）；接受 DONE/BLOCKED 前做一次完整的语义指纹比对，防止在过期画面上宣布完成。
- **文本按需生成、按路径优化。** 输入框清空在设备端一条 shell 完成（`MOVE_END` + 循环 `DEL`）；中文等非 ASCII 文本走 ADB Keyboard 广播；IME 只在首次输入时切换、结束时恢复。
- **语义指纹而非截图 diff。** 对"活动 + 可见文本 + 动作签名"做哈希；连续 3 步语义无变化且非 WAIT 即判定卡住并停止，不烧预算。

## 快速开始（复现步骤）

从零到跑通共五步：**装依赖 → 配密钥 → 配任务 → 连手机 → 运行**。

### 前置条件

- Python ≥ 3.9；
- 本机可用 adb：[platform-tools](https://developer.android.com/tools/releases/platform-tools) 下载解压即可，无需 Android Studio；
- 一台安卓手机：开启「开发者选项 → USB 调试」，数据线连接电脑，在手机弹窗上允许调试；
- 一个 [TypeSafe Jev](https://docs.typesafe.ai/) 的 API Key（必填）；任务涉及输入文字时，另备一个任意 OpenAI 兼容模型（DeepSeek、OpenRouter 等）的 Key。

### 1. 安装依赖并自检

```bash
cd jev-mobile
pip install -e .          # 用 uv 的话：uv sync（仓库自带 uv.lock）
pip install pytest
python -m pytest          # 离线测试，无需手机与密钥；全部通过说明环境就绪
```

### 2. 配置密钥（.env）

```bash
copy .env.example .env    # Windows CMD；Git Bash / macOS / Linux 用 cp
```

打开 `.env`：必填 `TYPESAFE_API_KEY`；需要打字时再填 `TEXT_MODEL_API_KEY`（默认对接 DeepSeek，改 `TEXT_MODEL_BASE_URL` 可指向任意 OpenAI 兼容接口）；adb 不在 PATH 时把 `ADB_PATH` 写成绝对路径。完整变量说明见[下文表格](#环境变量)。

### 3. 配置任务（config.yaml）

```bash
copy config.example.yaml config.yaml    # Git Bash / macOS / Linux 用 cp
```

最小配置只需改 `task` 一行，其余键保持默认（每个键的含义见 `config.example.yaml` 注释）：

```yaml
task: "打开设置，把飞行模式开关打开"
adb_path: 'C:\platform-tools\adb.exe'   # adb 已在 PATH 时可留空
```

`config.yaml` 放在运行目录或仓库根目录均可（两处都没有时走默认值，用 `--task` 也能直接跑）。

### 4. 连接手机并确认

```bash
adb devices               # 应列出设备且状态为 device；显示 unauthorized 时在手机上允许调试
```

### 5. 运行

```bash
python -m jev_mobile
```

正常输出形如：

```text
[   0.8s] 决策  CLICK 0.62 | DONE 0.15 | SCROLL_DOWN 0.11 | +6  conf=0.62  512ms  目标: [12] 0.93 [3] 0.04
[   1.3s] CLICK [12] 飞行模式 changed=True → Settings
...
status=done steps=2 decisions=8 elapsed=31.7s  决策均值 540ms
tokens 决策 in≈8600 out≈420 · 合计 in≈8600 out≈420
final: com.android.settings / Settings · "设置"
```

每步记录默认写入 `runs/<任务名>/`：`trace.json`（逐步观察、决策概率、执行动作、token 汇总）和逐步截图（`--record-dir` 会自动开启截图）。退出码 0 表示 status=done。

不想建配置文件时一行直接跑（其余参数见[下文](#命令行参数)）：

```bash
python -m jev_mobile --task "打开设置，把飞行模式开关打开" --start-package com.android.settings
```

### 可选加速件

- 设备安装 Droidrun / Mobilerun Portal —— A11Y 读取的主要加速手段（实测约 1s → 100ms~1s 量级，取决于设备与 Portal 版本）；未装时自动回退 `uiautomator dump`（部分设备单次数秒，仅保功能可用）；
- 设备安装 [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard) —— 支持中文输入，未安装时只能输 ASCII。

### 常见问题

| 现象 | 处理 |
| --- | --- |
| `adb devices` 显示 `unauthorized` | 在手机弹窗上允许调试；仍不行则在开发者选项里「撤销 USB 调试授权」后重插 |
| 报 `TYPESAFE_API_KEY is missing` | `.env` 没创建或没填 Key，确认它在运行目录下 |
| 每步观察前卡约 1s | 未装 Portal 时走 uiautomator 回退路径（慢）；设备安装 Portal 即可加速 |
| 已装 Portal，每步仍多约 1s | 这是逐帧截图；加 `--no-screenshots` 跳过截图、只留 `trace.json` |
| 中文输不进去 | 设备安装 ADB Keyboard（见上） |

## 使用

命令行参数优先于 `config.yaml`，可临时覆盖任意配置（优先级：命令行参数 > `config.yaml` > 环境变量）：

```bash
python -m jev_mobile --task "打开设置，把飞行模式开关打开" --record-dir runs/demo
```

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `--task` | 自然语言任务目标（必填） |
| `--start-package` | 起始 App 的包名，如 `com.android.settings`，首个观察前启动 |
| `--adb-path` / `--device` | 覆盖 `ADB_PATH` / `ANDROID_DEVICE` |
| `--record-dir` | 保存逐步截图与 `trace.json`；缺省自动按任务名存到 `runs/<任务名>/` |
| `--screenshots` | 观察时附带截图（`--record-dir` 隐含开启） |
| `--no-screenshots` | 录制时也跳过截图、只留 `trace.json`（每步截图约 1s，追求速度时开启） |
| `--action-interval` | 每个动作执行后额外等待的秒数，默认 0（环境变量 `ACTION_INTERVAL`） |

### 库

```python
from jev_mobile import Agent

with Agent("打开设置，把飞行模式开关打开", start_package="com.android.settings") as agent:
    for state in agent.run():
        last = state["history"][-1] if state["history"] else None
        print(state["elapsed_ms"], state["status"], last["action"] if last else "")
```

### 环境变量

`.env` 自动读取；命令行参数优先：

| 变量 | 用途 | 默认 |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | Jev 决策模型密钥（必填） | — |
| `TYPESAFE_MODEL` | 模型名 | `jev-latest` |
| `TEXT_MODEL_API_KEY` | 文本生成模型密钥（需要打字时必填） | — |
| `TEXT_MODEL_BASE_URL` | 任意 OpenAI 兼容接口 | `https://api.deepseek.com/v1` |
| `TEXT_MODEL` / `TEXT_MODEL_REASONING` | 文本模型名 / 是否关思考 | `deepseek-chat` / — |
| `ADB_PATH` / `ANDROID_DEVICE` | adb 路径 / 设备序列号 | `adb` / 空 |
| `ACTION_INTERVAL` | 每个动作执行后额外等待的秒数 | `0` |

## 代码地图

| 文件 | 职责 |
| --- | --- |
| `jev_mobile/agent.py` | 完整循环：predict → act → observe；DONE 新鲜度校验、预算与卡住检测 |
| `jev_mobile/a11y.py` | 一次快照 → 索引化动作空间（click/fill + 固定控件）、可见文本、语义指纹 |
| `jev_mobile/device.py` | ADB 连接、Portal/uiautomator 树读取、tap/swipe/键盘输入、IME 管理 |
| `jev_mobile/model.py` | TypeSafe 动态操作/目标头 + 小模型文本生成，选择结果校验 |
| `jev_mobile/config.py` | 加载 config.yaml（未知键报错，防拼写错误） |
| `jev_mobile/questions.py` | 决策与文本指令 |
| `scripts/jev_probe.py` | Jev 模型探测脚本：单独测试 Choice / Score / Noul 三种原语 |
| `scripts/bench_a11y.py` | A11Y 获取方案基准：Portal state_full vs uiautomator |

`jev_probe.py` 示例：

```bash
python scripts/jev_probe.py --question "选一个" --options 爬山 逛街      # Choice
python scripts/jev_probe.py --type score --question "严重吗" --options 轻 中 重
python scripts/jev_probe.py --type noul --question "这是紧急求助吗" --state "消息原文"
python scripts/jev_probe.py --demo                                       # 一次请求混发三种问题
```

## 设计边界

- **点击前不做逐元素重校验。** ADB 重读整棵 A11Y 树代价不可忽略，因此执行使用观察到的坐标，事后用语义指纹检测分歧；决策期间的窗口切换由焦点守卫捕获。
- **没有下拉选择（SELECT）操作。** Android 的下拉控件统一走点击流程。
- **上限：** 动作 60 步、决策 120 次、元素 250 个。
- **uiautomator 回退路径慢**（单次 dump 约 1s，部分设备可达数秒）；装 Portal 是主要加速手段，但 Portal 查询自身的耗时（约 100ms~1s，因设备而异）构成每步观察耗时的下限。
- **DONE 不是证明。** 模型宣布完成只代表它看到了可见证据；任务是否真正成功仍需独立验证（如检查任务要求的最终状态）。

## 开发

```bash
python -m pytest      # 离线测试，无需设备与网络
```

测试覆盖：动作空间构建（同一节点多操作共享一个索引）、选择结果校验、输入流程（文本助手 + 执行记录）、DONE 过期重判、动作预算与卡住检测等。
