# jev-mobile ⚡

**An Android phone agent: TypeSafe Jev makes fast A11Y decisions; a visual agent takes over after problems. Both execute over ADB.**

[中文文档](README.md)

Give it a natural-language goal such as "Open Settings and turn on Airplane Mode." [TypeSafe Jev](https://docs.typesafe.ai/) picks an operation and a target from the indexed element table. A small LLM supplies fast-path text. Configuring a visual model enables screenshot-based recovery with the original goal and executed history.

## Demo

For the task "在哔哩哔哩，播放龙卷风视频" (open Bilibili and play the *龙卷风* video): 18 seconds end to end, only 2–3s per action decision, no multimodal model involved at all, and a tiny token footprint.

<a href="docx/demo.mp4"><img src="docx/demo.gif" alt="Live demo: one natural-language goal; Jev decides and executes with one request per step" width="320" /></a>

[Watch the MP4](docx/demo.mp4)

## The action space

Every observation (one A11Y-tree snapshot) produces a fresh indexed element table:

```text
[1] EditText  query        · value=""
[2] TextView  Go
...

Operations: CLICK, TYPE_TEXT, SCROLL_UP, SCROLL_DOWN, BACK, HOME, ENTER, WAIT, DONE, BLOCKED
```

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
A11Y tree → table ──→│ operation (what to do)     │
                     │ click_target (tap what)    │
                     │ type_text_target (where)   │
                     └─────────────┬─────────────┘
                       consume only the target head
                       matching the chosen operation
                                   │
                    CLICK [2] ────┤──→ adb input tap
                TYPE_TEXT [1] ────┘
                          ↓
                 small LLM → text → adb keyboard
```

Target questions are speculative: if the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**; each target head contains only compatible elements. The fast path uses observed indexes; the visual path validates coordinate types and ranges. Both execute predefined actions rather than model-generated shell commands.

**Termination is a separate question.** A fast-path noul verdict ≥ 0.5 ends the task. A false verdict vetoes DONE and runs WAIT; after two vetoes, a third DONE goes to visual recovery. Without recovery, legacy third-DONE acceptance remains. Visual completion requires a planner or executor proposal followed by a separate verifier request on a freshly captured image, covering every original checklist item; rejection returns to planning.

## Fast decisions and visual recovery

The visual module independently implements the planning, execution, feedback, memory, image and inference mechanisms studied in local Mobilerun. Source, prompts and protocols were authored independently. It needs neither Mobilerun nor LlamaIndex; Pillow handles images. See the [implementation comparison](docs/visual-agent.md) (Chinese).

Set an image-capable model supporting OpenAI-compatible Chat Completions and JSON output in `.env`:

```dotenv
VISION_MODEL_API_KEY=your-key
VISION_MODEL_BASE_URL=https://openrouter.ai/api/v1
VISION_MODEL=your-vision-model
```

Recovery is enabled when both key and model are configured. It triggers on failed A11Y observation, missing click/input targets, model or execution errors, three unchanged actions, four clicks toggling the same control between two states, or persistent completion disagreement.

Requests receive role-specific context while preserving the original goal and constraints. Planner/verifier receive compact full-history summaries and three recent outcomes; executor receives the active plan, current elements, two recent outcomes and earlier entered values. Full audit details remain in the trace. Planner memory is a replacement snapshot of up to 20 facts with stable keys, avoiding accumulation of paraphrases. Supported device actions include point/area click, adjustable long press/swipe/wait, focused/replacement/append input, installed-app launch, back, home and enter. The executor cannot finish a task. Integer coordinates use 0–1000 and map to native PNG dimensions; resizing preserves the full image.

The planner creates a stage plan once; the executor continues from fresh observations, advancing stages only with observed evidence. Deviations, execution failures, stalls, stale actions or rejected completion trigger replanning. Executor replies use `status: act|replan|complete`, a cumulative summary and consecutive `completed_steps`; only act includes a device action. Completion proposals always require independent review.

Visual mode takes over for the remaining task. Both modes share 60 device-action attempts and 240 logical decision-model attempts, including all roles, failed responses and schema corrections. Each role gets at most three response attempts; execution errors, stalls and rejected completion feed back into planning, stopping after three recovery feedbacks without detected change. `--no-screenshots` affects fast-path images only. Disable recovery with `--no-vision-fallback`. Traces preserve role responses, usage, subgoals, before/after pages and errors.

Run the visual workflow from the first observation with `python -m jev_mobile --vision-only --task "Your task"`, or set `vision_only: true`. This needs only the visual key/model. Prompts live in `jev_mobile/visual_prompts.py`; optional `VISION_PLANNER_MODEL`, `VISION_EXECUTOR_MODEL` and `VISION_VERIFIER_MODEL` override the shared model using the same endpoint/key.

Visual requests also carry current indexed A11Y elements, using the same numbering as Jev. CLICK, LONG_PRESS and TYPE_TEXT support integer index targets; CLICK/LONG_PRESS also accept coordinates, and indexed input reads the field's current value from the snapshot. Indexed actions refresh the tree before dispatch and replan when the table, focus or dimensions changed. A11Y is optional: a failed probe disables further probes on this Device instance while screenshot/coordinate actions continue; a new Device retries. See the [index/coordinate contract](docs/visual-agent.md#a11y-编号与坐标共同执行).

## Why it moves

- **One model request per step.** The operation head, every target head, and the independent goal-achieved judgment (noul) share the same observed state and are evaluated in parallel within a single request.
- **No screenshots in the default loop.** Jev consumes structured state: className, text, contentDescription, resourceId, bounds, checked/selected, and related semantic properties.
- **One A11Y read covers the whole state, and three reads run in parallel.** Prefer the Portal content provider (`com.mobilerun.portal` / `com.droidrun.portal`): a single `content query` returns the full tree plus phone state; fall back to `uiautomator dump /dev/tty` (one round trip streams the XML back) when absent. Tree, focused window, and screenshot are fetched concurrently, so one observation costs the slowest read, not the sum.
- **Cheap freshness guards.** Compare window focus before predicting and semantic fingerprints before fast-path stops. Visual stops check window focus, since pixel equality rejects animated screens; changes within the same window can still make a decision stale.
- **Text is generated only when needed and optimized on the path.** Clearing a field happens in one device-side shell (`MOVE_END` + repeated `DEL`); non-ASCII text goes through an ADB Keyboard broadcast; the IME is switched once on first input and restored on close.
- **Fast-path semantic fingerprints.** Three unchanged non-WAIT steps or a two-state click cycle trigger recovery, or stop when disabled. Visual stalls use grayscale summaries before adding the grid, followed by replanning. Animations can still look like progress; shared budgets bound repeated actions.

## Quickstart (reproducing this project)

Five steps from zero to a finished run: **install → keys → task → device → run**.

### Prerequisites

- Python ≥ 3.9;
- a working adb: download [platform-tools](https://developer.android.com/tools/releases/platform-tools) and unzip — no Android Studio needed;
- an Android phone with Developer options → USB debugging enabled, connected over USB, debugging prompt accepted;
- a [TypeSafe Jev](https://docs.typesafe.ai/) API key (required); plus a key for any OpenAI-compatible model (DeepSeek, OpenRouter, …) when the task involves typing.

### 1. Install and self-check

```bash
cd jev-mobile
pip install -e .          # with uv: uv sync (the repo ships uv.lock)
pip install pytest
python -m pytest          # offline; no device or keys needed — all green means the environment is ready
```

### 2. Keys (.env)

```bash
cp .env.example .env      # Windows CMD: copy .env.example .env
```

Fill in `TYPESAFE_API_KEY` (required). Add `TEXT_MODEL_API_KEY` when the task types text (defaults to DeepSeek; point `TEXT_MODEL_BASE_URL` at any OpenAI-compatible endpoint). Set `ADB_PATH` to an absolute path when adb is not on PATH. Full table [below](#environment-variables).

### 3. Task (config.yaml)

```bash
cp config.example.yaml config.yaml    # Windows CMD: copy
```

The minimal config changes only `task`; every other key keeps its default (each key is commented in `config.example.yaml`):

```yaml
task: "Open Settings and turn on Airplane Mode"
adb_path: 'C:\platform-tools\adb.exe'   # leave empty when adb is on PATH
```

`config.yaml` is read from the working directory or the repo root; with neither present, defaults apply and `--task` alone works.

### 4. Connect the phone

```bash
adb devices               # must list the device as `device`; accept the prompt when it shows `unauthorized`
```

### 5. Run

```bash
python -m jev_mobile
```

Typical output (console labels are Chinese):

```text
[   0.8s] 决策  CLICK 0.62 | DONE 0.15 | SCROLL_DOWN 0.11 | +6  conf=0.62  512ms  目标: [12] 0.93 [3] 0.04  goal=0.02
[   1.3s] CLICK [12] 飞行模式 changed=True → Settings
...
status=done steps=2 decisions=8 elapsed=31.7s  决策均值 540ms
tokens 决策 in≈8600 out≈420 · 合计 in≈8600 out≈420
final: com.android.settings / Settings · "设置"
```

Per-step records default to `runs/<task-name>/`: `trace.json` (per-step observations, decision probabilities, executed actions, and a token-usage summary) plus screenshots (`--record-dir` turns screenshots on automatically). Exit code 0 means status=done.

One-liner without a config file (all flags [below](#cli-flags)):

```bash
python -m jev_mobile --task "Open Settings and turn on Airplane Mode" --start-package com.android.settings
```

### Optional accelerators

- Droidrun / Mobilerun Portal on the device — the main A11Y-read accelerator (measured roughly 1s → the 100ms–1s range, depending on device and Portal version); without it the loop falls back to `uiautomator dump` (several seconds per dump on some devices; keeps the feature working, barely);
- [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard) on the device — enables non-ASCII input; without it only ASCII can be typed.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `adb devices` shows `unauthorized` | Accept the debugging prompt on the phone; or revoke USB debugging authorizations and replug |
| `TYPESAFE_API_KEY is missing` | `.env` not created or key unfilled; make sure it sits in the working directory |
| ~1s stall before each observation | No Portal installed, so the slow uiautomator fallback is used; install Portal on the device |
| Portal installed but still ~1s per step | That is the per-step screenshot; pass `--no-screenshots` to keep only `trace.json` |
| Chinese text won't type | Install ADB Keyboard (see above) |

## Use

CLI flags override `config.yaml` (priority: CLI flags > `config.yaml` > environment variables):

```bash
python -m jev_mobile --task "Open Settings and turn on Airplane Mode" --record-dir runs/demo
```

### CLI flags

| Flag | Purpose |
| --- | --- |
| `--task` | Natural-language goal (required) |
| `--start-package` | Package to launch before the first observation, e.g. `com.android.settings` |
| `--adb-path` / `--device` | Override `ADB_PATH` / `ANDROID_DEVICE` |
| `--record-dir` | Save per-step screenshots and `trace.json`; defaults to `runs/<task-name>/` |
| `--screenshots` | Observe with screenshots (implied by `--record-dir`) |
| `--no-screenshots` | Skip fast-path screenshots; visual recovery still captures images |
| `--no-vision-fallback` | Disable visual recovery; `--no-screenshots` alone still allows visual screenshots |
| `--vision-only` | Start with visual planning/execution/review; no Jev key required |
| `--action-interval` | Extra seconds to wait after each executed action, default 0 (env: `ACTION_INTERVAL`) |

### Library

```python
from jev_mobile import Agent

with Agent("Open Settings and turn on Airplane Mode", start_package="com.android.settings") as agent:
    for state in agent.run():
        last = state["history"][-1] if state["history"] else None
        print(state["elapsed_ms"], state["status"], last["action"] if last else "")
```

### Environment variables

Loaded automatically from `.env`; CLI flags win:

| Variable | Purpose | Default |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | Jev decision-model key (required) | — |
| `TYPESAFE_MODEL` | Model name | `jev-latest` |
| `TEXT_MODEL_API_KEY` | Text-model key (required when typing) | — |
| `TEXT_MODEL_BASE_URL` | Any OpenAI-compatible endpoint | `https://api.deepseek.com/v1` |
| `TEXT_MODEL` / `TEXT_MODEL_REASONING` | Text model / disable thinking | `deepseek-chat` / — |
| `VISION_MODEL_API_KEY` / `VISION_MODEL` | Visual key / image-capable model; both enable recovery | — |
| `VISION_MODEL_BASE_URL` | OpenAI-compatible visual endpoint | `https://openrouter.ai/api/v1` |
| `VISION_PLANNER_MODEL` / `VISION_EXECUTOR_MODEL` / `VISION_VERIFIER_MODEL` | Optional role models sharing endpoint/key | `VISION_MODEL` |
| `VISION_MODEL_TIMEOUT` | Visual timeout, 1–300 seconds | `90` |
| `VISION_IMAGE_MAX_SIDE` | Longest image edge, 320–4096; no cropping | `1600` |
| `VISION_CHANGE_THRESHOLD` | Mean grayscale difference, 0–255; stall detection only | `3` |
| `ADB_PATH` / `ANDROID_DEVICE` | adb path / device serial | `adb` / empty |
| `ACTION_INTERVAL` | Extra seconds to wait after each executed action | `0` |

## Code map

| File | Responsibility |
| --- | --- |
| `jev_mobile/agent.py` | The complete loop: predict → act → observe; independent goal-gated termination, DONE freshness check, budgets, stuck detection |
| `jev_mobile/a11y.py` | One snapshot → indexed action space (click/fill + fixed controls), visible text, semantic fingerprint |
| `jev_mobile/device.py` | ADB connection, Portal/uiautomator tree reads, tap/swipe/keyboard input, IME management |
| `jev_mobile/model.py` | TypeSafe dynamic operation/target heads + small-model text generation, answer validation |
| `jev_mobile/vision.py` | Planning/execution/review, shared context, protocol validation and recovery |
| `jev_mobile/visual_prompts.py` | Original planner/executor/verifier prompts |
| `jev_mobile/visual_images.py` | Resizing, grid, native dimensions and change detection |
| `jev_mobile/config.py` | Loads config.yaml (unknown keys are rejected to catch typos) |
| `jev_mobile/questions.py` | Decision and text instructions |
| `scripts/jev_probe.py` | Jev probe: test the Choice / Score / Noul primitives standalone |
| `scripts/bench_a11y.py` | A11Y capture benchmark: Portal state_full vs uiautomator |

`jev_probe.py` examples:

```bash
python scripts/jev_probe.py --question "Pick one" --options hiking shopping   # Choice
python scripts/jev_probe.py --type score --question "How severe?" --options low medium high
python scripts/jev_probe.py --type noul --question "Is this urgent?" --state "message text"
python scripts/jev_probe.py --demo                                          # all three in one request
```

## Design boundaries

- **Freshness depends on target type.** Visual indexed actions refresh A11Y before dispatch, rejecting changed tables, windows or dimensions. Fast-path and visual coordinate actions retain focus guards and post-action change detection; layout changes within a window can still stale their coordinates. Tree refresh adds overhead.
- **No SELECT operation.** Android dropdowns go through the click flow.
- **Limits:** 60 device-action attempts, 240 logical decision-model attempts, 250 fast-path elements. Fast-path text helpers are counted separately and bounded by the action limit.
- **The uiautomator fallback is slow** (~1s per dump, several seconds on some devices); Portal is the main accelerator, but the Portal query's own latency (~100ms–1s depending on the device) sets the floor for each observation.
- **Completion is still a model judgment.** Visual mode now has separate review on fresh images; two judgments by the same model can share errors. Normal fast-path completion does not invoke visual review; use vision-only mode to review every finish. No automatic return to Jev, cloud/iOS/MCP/skill-service port or measured real-device success-rate parity is claimed.

## Development

```bash
python -m pytest      # offline; no device or network needed
```

The tests cover action-space construction (one node, one index, multiple operations), answer validation, the typing flow (text helper + execution record), stale-DONE re-deciding, and action budgets and stuck detection.
