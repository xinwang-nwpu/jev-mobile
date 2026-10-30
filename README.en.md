<h1 align="center">Jev Mobile</h1>

<p align="center">
  Give your Android phone a task in plain language.<br>
  Models inspect the current screen, choose actions, and hand over to a visual agent when needed.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#choose-a-model">Choose a model</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#run-records">Run records</a> ·
  <a href="android/jev-bridge/README.md">Jev Bridge</a> ·
  <a href="docs/visual-agent.md">Visual agent</a> ·
  <a href="README.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Android-8.0%2B-3DDC84?logo=android&logoColor=white" alt="Android 8.0 and later">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9 and later">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/xinwang-nwpu/jev-mobile" alt="License"></a>
</p>

---

```powershell
python -m jev_mobile --task "Open Bilibili and search for Jay Chou's Tornado"
```

Jev Mobile reads the screen and accessibility nodes, chooses actions such as tapping, typing, and scrolling, and interacts with the phone through ADB. Use cloud-hosted Jev, a local multimodal OneJev service, or a visual model from the first step.

<p align="center">
  <img src="docx/demo.gif" width="320" alt="Jev Mobile operating an Android phone">
  <br><a href="docx/demo.mp4">Watch the full demonstration</a>
</p>

> The demonstration shows one run. Actual speed and results depend on the device, page, network, and models.

## What it can do

- **Operate from a task**: select actionable nodes on the current screen, tap, type, scroll, go back, or wait, then observe again.
- **Choose a model**: use TypeSafe Jev, local OneJev with screenshots and A11Y candidates, or the visual agent alone.
- **Recover and review**: hand over to the visual agent when the fast path fails or gets stuck; review OneJev's completion proposals with a visual model.
- **Inspect each run**: Jev Bridge supplies accessibility trees and Unicode input; run records capture screenshots, nodes, actions, timings, and model calls.

## Quick start

The following example uses Windows PowerShell and a connected Android phone. See [config.example.yaml](config.example.yaml) and [.env.example](.env.example) for all settings.

### 1. Prepare the phone and environment

- Python 3.9 or later and [Android platform-tools](https://developer.android.com/tools/releases/platform-tools), with `adb` available.
- An Android phone with developer options and USB or wireless debugging enabled. Accept the device's debugging authorization prompt.
- We recommend installing [Jev Bridge](android/jev-bridge/README.md) and enabling its accessibility service and keyboard for page reading and Unicode input. Other supported channels can be used as fallbacks.

```powershell
adb devices
```

Confirm that the device is listed as `device`. For wireless debugging, pair and connect it first using the phone's instructions.

### 2. Install the project

```powershell
git clone https://github.com/xinwang-nwpu/jev-mobile.git
cd jev-mobile
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

### 3. Configure models

Edit `.env`. For example, to use TypeSafe Jev as the fast model:

```dotenv
FAST_MODEL_BACKEND=jev
FAST_MODEL_URL=https://api.typesafe.ai/v1/systemone
FAST_MODEL_NAME=jev-latest
FAST_MODEL_API_KEY=your-key
```

Text entry also needs `TEXT_MODEL_API_KEY`, `TEXT_MODEL_BASE_URL`, and `TEXT_MODEL`. Visual recovery and completion review need `VISION_MODEL_API_KEY`, `VISION_MODEL_BASE_URL`, and an image-capable `VISION_MODEL`. These services must provide the OpenAI-compatible Chat Completions interface used by the project.

For local OneJev or visual-only operation, see [Choose a model](#choose-a-model). Keep credentials in your local `.env` and out of version control.

### 4. Connect and run

Set `adb_path` in `config.yaml`. Leave `device` empty for one device, or use the serial shown by `adb devices` when several devices are connected. Then run:

```powershell
python -m jev_mobile --task "Open Settings"
```

The program prints decisions, actions, and timings. By default it saves run records under `runs/<task-name>/`. A `done` status means the execution flow judged the task complete; inspect the saved screenshots and trace to review the result.

## Choose a model

| Mode | Input and execution | Requirements |
| --- | --- | --- |
| **TypeSafe Jev** | Chooses actions and targets from A11Y elements; the fast path includes an independent completion judgment | TypeSafe endpoint and key; text model for text entry |
| **OneJev** | Uses screenshots and A11Y action candidates to choose an operation and target together | Local SystemOne service; text model for text entry; visual model for completion review |
| **Visual-only** | Uses screenshots and available A11Y indexes from the first step; the visual agent executes and reviews | Image-capable visual model with JSON output |

### Use local OneJev

Deploy and start a OneJev service that supports the SystemOne interface. Model files and the inference service are not included in this repository. Then configure `.env`:

```dotenv
FAST_MODEL_BACKEND=onejev
FAST_MODEL_URL=http://127.0.0.1:8000/v1/systemone
FAST_MODEL_NAME=OneJev1.6-4B-Q4_K_M
FAST_MODEL_API_KEY=
```

Set `vision_only: false` and `vision_fallback: true` in `config.yaml`, and configure a visual model. OneJev requires screenshots on each decision and cannot be used with `--no-screenshots`. It does not request a separate task-completion probability; a `DONE` proposal is handed to the visual agent for review.

### Start with the visual agent

After configuring `VISION_MODEL_*`, run:

```powershell
python -m jev_mobile --vision-only --task "Open Settings"
```

Alternatively, set `vision_only: true` in `config.yaml`. `vision_mode: fast` skips the Planner and uses an Executor with completion review; `planned` uses a Planner, Executor, and Verifier. See the [visual-agent documentation](docs/visual-agent.md) for roles and prompts.

## How it works

```text
User task
   ↓
Observe: screenshot / A11Y tree → select actionable nodes → assign temporary indexes
   ↓
Decide: Jev or OneJev selects an operation and target
   ↓
Execute: ADB taps and swipes; Jev Bridge or another input channel enters text
   ↓
Observe again → next step
   ↓ when recovery or review is needed
Visual agent takes over → execute / review completion
```

The fast path supports tapping, text entry, scrolling, back, home, enter, and waiting. The visual path also supports tapping, long pressing, typing, swiping, and launching apps using coordinates or current A11Y indexes. Indexes are assigned for each observation; the executor runs predefined actions.

Screenshots can include a coordinate grid and A11Y index boxes, controlled by `vision_a11y_boxes` in `config.yaml`. Screenshots and A11Y trees are read separately and can differ while a page is changing. Check the actual target and result for actions such as sending messages.

## Run records

The default directory is `runs/<task-name>/`; use `--record-dir` to choose another location:

| File | Contents |
| --- | --- |
| `trace.json` | Actions, targets, results, model calls, handoff reasons, and timings |
| `*.a11y.json` | Attributes, `actionList`, and duplicate markers for nodes in the current action table |
| Screenshots | Page images from the visual path and fast paths with screenshots enabled |

An action's `target_node` records the target's full resource ID, class name, original text and description, native bounds, and clickable, editable, enabled, checked, selected, and focused states. It is empty when a coordinate action has no associated node.

Duplicate detection compares visible, enabled nodes with valid bounds. Nodes are duplicates when `resourceId`, `className`, `text`, and `contentDescription` all match exactly. Capture the current page separately with duplicate annotations and a boxed screenshot:

```powershell
python script/capture_a11y.py --device DEVICE_SERIAL
```

The capture script uses red boxes for duplicates and green for unique nodes. The agent's colored index boxes identify operation targets; they serve a different purpose.

## Common configuration

```yaml
vision_only: false       # true: use the visual model from the first step
vision_fallback: true    # allow visual recovery when the fast path fails or stalls
vision_mode: fast        # fast or planned
vision_a11y_boxes: true  # show actionable-node indexes on screenshots
```

Other options include `--device`, `--adb-path`, `--record-dir`, `--start-package`, `--no-vision-fallback`, and `--quiet`. See all command-line arguments with:

```powershell
python -m jev_mobile --help
```

If `adb devices` shows `unauthorized`, confirm debugging authorization on the phone. If Unicode input fails, check that the Jev Bridge keyboard is enabled and selected. See [Jev Bridge](android/jev-bridge/README.md) for installation and API details.

## Project layout

```text
jev_mobile/           Agent loop, model requests, device communication, and A11Y processing
android/jev-bridge/   Android companion app
script/               Page-tree capture and annotation
tests/                Python tests
docs/                 Visual implementation, performance analysis, and research notes
```
