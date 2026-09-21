"""Run one task on a connected phone.

    python -m jev_mobile                     # reads config.yaml (CWD or package root)
    python -m jev_mobile --task "..."        # CLI flags override the file

Credentials come from the environment or a local .env file. TYPESAFE_API_KEY is
required; TEXT_MODEL_API_KEY is required only when the agent types text.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from .agent import Agent
from .config import load_config


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


# ---------------------------------------------------------------------------
# Output formatting: probabilities are the model's reasoning, so show them.
# ---------------------------------------------------------------------------


def format_probability_list(probabilities: Dict[str, float], limit: int = 4) -> str:
    ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
    shown = " | ".join("%s %.2f" % (name, value) for name, value in ranked[:limit])
    hidden = len(ranked) - limit
    if hidden > 0:
        shown += " | +%d" % hidden
    return shown


def format_event(event: Dict) -> str:
    elapsed = "[%6.1fs]" % (event.get("elapsed_ms", 0) / 1000)
    kind = event["type"]
    if kind == "decision":
        operations = format_probability_list(event.get("operation_probabilities", {}))
        line = "%s 决策  %s  conf=%.2f  %dms" % (elapsed, operations, event.get("confidence", 0), event.get("latency_ms", 0))
        targets = event.get("target_probabilities") or {}
        if targets:
            ranked = sorted(targets.items(), key=lambda kv: -kv[1])[:3]
            line += "  目标: " + " ".join("[%s] %.2f" % (index, prob) for index, prob in ranked)
        return line
    if kind == "stale_done":
        return "%s DONE 被拒绝：决策后屏幕已变化，重新观察" % elapsed
    if kind == "stuck":
        return "%s 判定卡住：连续 3 步无变化 → blocked" % elapsed
    if kind == "reobserve":
        return "%s 焦点窗口变化，重新观察" % elapsed
    return "%s %s" % (elapsed, kind)


def format_step(step: Dict, quiet: bool) -> str:
    elapsed = "[%6.1fs]" % (step["elapsed_ms"] / 1000)
    if quiet:
        parts = [elapsed, step["operation"]]
        if step["target"]:
            parts.append("[%s]" % step["target"])
        parts.append(step["action"])
        if step["text"]:
            parts.append("text=%r" % step["text"])
        parts.append("changed=%s" % step["page_changed"])
        return " ".join(str(p) for p in parts)
    parts = [elapsed, step["operation"]]
    if step["target"]:
        parts.append("[%s]" % step["target"])
    parts.append(step["action"])
    if step["text"]:
        note = "(%s %dms)" % (step["text_helper"], step["text_latency_ms"]) if step["text_helper"] else ""
        parts.append("text=%r %s" % (step["text"], note.strip()))
    parts.append("changed=%s" % step["page_changed"])
    activity = step.get("activity") or ""
    if activity and activity != step.get("activity_before"):
        parts.append("→ %s" % activity.rsplit(".", 1)[-1])
    return " ".join(str(p) for p in parts)


def format_summary(state: Dict, quiet: bool) -> str:
    decisions = state["decisions"]
    line = "status=%s steps=%d decisions=%d elapsed=%.1fs" % (
        state["status"],
        len(state["history"]),
        len(decisions),
        state["elapsed_ms"] / 1000,
    )
    if not quiet and decisions:
        latencies = [d["latency_ms"] for d in decisions]
        tokens_in = sum(d.get("usage", {}).get("input_tokens", 0) or 0 for d in decisions)
        tokens_out = sum(d.get("usage", {}).get("output_tokens", 0) or 0 for d in decisions)
        line += "  决策均值 %dms  tokens in≈%d out≈%d" % (sum(latencies) // len(latencies), tokens_in, tokens_out)
        text_calls = state["text_calls"]
        if text_calls:
            text_ms = sum(t["latency_ms"] for t in text_calls)
            line += "  文本生成 %d 次均值 %dms" % (len(text_calls), text_ms // len(text_calls))
    return line


def final_page_line(state: Dict) -> str:
    page = state["page"]
    text = (page.get("text") or "").split("\n", 1)[0][:60]
    return 'final: %s / %s · "%s"' % (page.get("app", ""), page.get("activity", "").rsplit(".", 1)[-1], text)


def task_run_dir(task: str, base: str = "runs") -> str:
    """Default record folder named after the task, safe for Windows paths."""
    invalid = '<>:"/\\|?*'
    name = "".join("_" if ch in invalid else ch for ch in task)
    name = name.strip().replace(" ", "_").strip("._")
    name = name[:60] or "mobile_task"
    return os.path.join(base, name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one phone task with Jev decisions over A11Y state.")
    parser.add_argument("--task", default=None, help="Natural-language goal; overrides config.yaml.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    parser.add_argument("--adb-path", default=None, help="adb executable; overrides config.yaml.")
    parser.add_argument("--device", default=None, help="adb device serial; overrides config.yaml.")
    parser.add_argument("--start-package", default=None, help="Launch this package before the first observation.")
    parser.add_argument("--record-dir", default=None, help="Save screenshots and trace.json here.")
    parser.add_argument("--screenshots", action="store_true", help="Observe with screenshots (implied by --record-dir).")
    parser.add_argument(
        "--action-interval",
        type=float,
        default=None,
        help="Extra seconds to wait after each executed action; overrides config.yaml.",
    )
    parser.add_argument("--quiet", action="store_true", help="One line per action, no decision details.")
    args = parser.parse_args(argv)

    load_env_file()
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        print("Bad config file: %s" % error, file=sys.stderr)
        return 1
    task = args.task or config["task"]
    if not task:
        print("No task: set `task` in config.yaml or pass --task.", file=sys.stderr)
        return 1
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY is missing; set it in the environment or .env.", file=sys.stderr)
        return 1

    record_dir = args.record_dir or config["record_dir"] or task_run_dir(task)
    interval = args.action_interval if args.action_interval is not None else config["action_interval"]
    quiet = args.quiet or config["quiet"]
    agent = Agent(
        task,
        adb_path=args.adb_path or config["adb_path"] or None,
        serial=args.device or config["device"],
        start_package=args.start_package or config["start_package"],
        record_dir=record_dir,
        screenshots=args.screenshots or config["screenshots"],
        action_interval=interval,
    )
    last_step = 0
    last_event = 0
    try:
        for _ in agent.run():
            state = agent.state
            if not quiet:
                for event in state["events"][last_event:]:
                    print(format_event(event))
                last_event = len(state["events"])
            history = state["history"]
            for step in history[last_step:]:
                print(format_step(step, quiet))
            last_step = len(history)
    except (RuntimeError, ValueError) as error:
        print("Run stopped: %s" % error, file=sys.stderr)
        status = agent.state["status"]
    else:
        status = agent.state["status"]
    finally:
        agent.close()
        if record_dir:
            Path(record_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(record_dir) / "trace.json", "w", encoding="utf-8") as f:
                json.dump(agent.trace(), f, ensure_ascii=False, indent=2)
    state = agent.state
    print(format_summary(state, quiet))
    if not quiet:
        print(final_page_line(state))
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
