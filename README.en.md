# jev-mobile ⚡

**An Android phone agent: one TypeSafe Jev request decides both the operation and its target element — no screenshots, pure structured A11Y-tree state, executed directly over ADB.**

[中文文档](README.md)

Give it a natural-language goal such as "Open Settings and turn on Airplane Mode." [TypeSafe Jev](https://docs.typesafe.ai/) picks an operation and a target from the indexed element table; only when the operation is `TYPE_TEXT` does a small LLM generate the text to enter. The loop never depends on screenshot recognition, so decisions and execution stay fast.

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

Target questions are speculative: if the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**; each target head contains only elements compatible with that operation. Element indices are assigned by code (positions within the snapshot), and model output is always an already-observed index — never a selector, coordinate, or shell command, so there is no execution path for UI-text injection.

## Why it moves

- **One model request per step.** The operation head and every target head share the same observed state and are evaluated in parallel within a single request.
- **No screenshots in the default loop.** Jev consumes structured state: className, text, contentDescription, resourceId, bounds, checked/selected, and related semantic properties.
- **One A11Y read covers the whole state.** Prefer the Portal content provider (`com.mobilerun.portal` / `com.droidrun.portal`): a single `content query` returns the full tree plus phone state; fall back to a `uiautomator dump` when absent.
- **Cheap freshness guards.** Before predicting, compare the `dumpsys window` focus; before accepting DONE/BLOCKED, run one full semantic-fingerprint comparison so the agent never declares success on a stale screen.
- **Text is generated only when needed and optimized on the path.** Clearing a field happens in one device-side shell (`MOVE_END` + repeated `DEL`); non-ASCII text goes through an ADB Keyboard broadcast; the IME is switched once on first input and restored on close.
- **Semantic fingerprints, not screenshot diffs.** A hash over "activity + visible text + action signatures"; three consecutive steps with no semantic change and no WAIT means stuck — the run stops instead of burning budget.

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
[   0.8s] 决策  CLICK 0.62 | DONE 0.15 | SCROLL_DOWN 0.11 | +6  conf=0.62  512ms  目标: [12] 0.93 [3] 0.04
[   1.3s] CLICK [12] 飞行模式 changed=True → Settings
...
status=done steps=2 decisions=8 elapsed=31.7s  决策均值 540ms  tokens in≈8600 out≈420
final: com.android.settings / Settings · "设置"
```

Per-step records default to `runs/<task-name>/`: `trace.json` (per-step observations, decision probabilities, executed actions) plus screenshots (`--record-dir` turns screenshots on automatically). Exit code 0 means status=done.

One-liner without a config file (all flags [below](#cli-flags)):

```bash
python -m jev_mobile --task "Open Settings and turn on Airplane Mode" --start-package com.android.settings
```

### Optional accelerators

- Droidrun / Mobilerun Portal on the device — drops an A11Y read from ~1s to ~100ms (without it the loop falls back to `uiautomator dump`: same behavior, just slower);
- [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard) on the device — enables non-ASCII input; without it only ASCII can be typed.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `adb devices` shows `unauthorized` | Accept the debugging prompt on the phone; or revoke USB debugging authorizations and replug |
| `TYPESAFE_API_KEY is missing` | `.env` not created or key unfilled; make sure it sits in the working directory |
| ~1s stall before each observation | The uiautomator fallback is slow; install Portal on the device |
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
| `ADB_PATH` / `ANDROID_DEVICE` | adb path / device serial | `adb` / empty |
| `ACTION_INTERVAL` | Extra seconds to wait after each executed action | `0` |

## Code map

| File | Responsibility |
| --- | --- |
| `jev_mobile/agent.py` | The complete loop: predict → act → observe; DONE freshness check, budgets, stuck detection |
| `jev_mobile/a11y.py` | One snapshot → indexed action space (click/fill + fixed controls), visible text, semantic fingerprint |
| `jev_mobile/device.py` | ADB connection, Portal/uiautomator tree reads, tap/swipe/keyboard input, IME management |
| `jev_mobile/model.py` | TypeSafe dynamic operation/target heads + small-model text generation, answer validation |
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

- **No per-element freshness check before a tap.** Re-reading the whole A11Y tree over ADB is not cheap, so execution uses the observed coordinates and divergence is detected afterwards via the semantic fingerprint; window switches during a decision are caught by the focus guard.
- **No SELECT operation.** Android dropdowns go through the click flow.
- **Limits:** 60 actions, 120 decisions, 250 elements per run.
- **The uiautomator path is slower** (~1s per dump); installing Portal speeds it up significantly.
- **DONE is not proof.** The model declaring DONE only means it saw visible evidence; whether the task truly succeeded still needs independent verification (e.g. checking the required final state).

## Development

```bash
python -m pytest      # offline; no device or network needed
```

The tests cover action-space construction (one node, one index, multiple operations), answer validation, the typing flow (text helper + execution record), stale-DONE re-deciding, and action budgets and stuck detection.
