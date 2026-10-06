"""Run one task on a connected phone.

    python -m jev_mobile                     # reads config.yaml (CWD or package root)
    python -m jev_mobile --task "..."        # CLI flags override the file

Credentials come from the environment or a local .env file. Fast mode needs Jev;
vision-only mode needs the visual model key/name and generates its own field text.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from .agent import Agent, usage_totals
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
        goal_probability = event.get("goal_probability")
        if goal_probability is not None:
            line += "  goal=%.2f" % goal_probability
        return line
    if kind == "stale_done":
        return "%s DONE 被拒绝：决策后屏幕已变化，重新观察" % elapsed
    if kind == "handoff":
        return "%s 切换到视觉 Agent：%s" % (elapsed, event["reason"])
    if kind == "visual_decision":
        role = {"planner": "规划器", "executor": "执行器", "verifier": "复核器"}.get(event.get("role"), "模型")
        return "%s 视觉决策 %s  %s  %s请求 %dms  %s" % (
            elapsed, event["operation"], event["model"], role, event["latency_ms"], event.get("reason") or "")
    if kind == "visual_plan":
        return "%s 视觉规划（请求 %dms）：%s · %s" % (elapsed, event.get("latency_ms", 0), event["summary"], event["subgoal"])
    if kind == "visual_progress":
        return "%s 视觉进度 %d/%d：%s" % (elapsed, event["completed"], event["total"], event["summary"])
    if kind == "visual_review":
        return "%s 完成复核 %s（请求 %dms）：%s" % (elapsed, event["status"], event.get("latency_ms", 0), event["reason"])
    if kind == "round_timing":
        return "%s 本轮 %d：总耗时 %dms · 模型 %dms（%d 次请求）· 设备阶段 %dms · 其他 %dms" % (
            elapsed, event["round"], event["duration_ms"], event["model_ms"], event["requests"], event["device_ms"], event["other_ms"])
    if kind == "visual_retry":
        line = "%s 视觉 %s 请求失败（第 %d 次）：%s" % (elapsed, event["role"], event["attempt"], event["error"])
        if "retry_action" in event:
            adjustments = {"repair_json": "修正 JSON", "retry_request": "重试请求", "disable_reasoning": "关闭思考后重试",
                           "increase_output_limit": "提高输出预算后重试", "stop": "停止重试"}
            line += " · finish=%s 思考tokens=%s 输出上限=%s · %s" % (
                event.get("finish_reason"), event.get("reasoning_tokens"), event.get("max_tokens"),
                adjustments.get(event["retry_action"], event["retry_action"]))
        return line
    if kind == "visual_replan":
        return "%s 视觉恢复重规划 %s：%s" % (elapsed, event["kind"], event["message"])
    if kind == "done_vetoed":
        return "%s DONE 被目标判定否决（未达成，等待后重判）goal=%.2f" % (elapsed, event.get("goal_probability") or 0.0)
    if kind == "goal_done":
        return "%s 目标判定已达成，结束（操作头仍想行动）goal=%.2f" % (elapsed, event.get("goal_probability") or 0.0)
    if kind == "stuck":
        return "%s 判定卡住：连续 3 步无变化 → blocked" % elapsed
    if kind == "cycle":
        return "%s 检测到循环：反复点击 %s，页面在两个状态间切换 → blocked" % (elapsed, event.get("action", ""))
    if kind == "reobserve":
        return "%s 焦点窗口变化，重新观察" % elapsed
    return "%s %s" % (elapsed, kind)


def format_step(step: Dict, quiet: bool) -> str:
    elapsed = "[%6.1fs]" % (step["elapsed_ms"] / 1000)
    operation = ("视觉 " if step.get("mode") == "vision" else "") + step["operation"]
    if quiet:
        parts = [elapsed, operation]
        if step["target"]:
            parts.append("[%s]" % step["target"])
        parts.append(step["action"])
        if step["text"]:
            parts.append("text=%r" % step["text"])
        parts.append("changed=%s" % step["page_changed"])
        if step.get("success") is False:
            parts.append("failed=%s" % step.get("error"))
        return " ".join(str(p) for p in parts)
    parts = [elapsed, operation]
    if step["target"]:
        parts.append("[%s]" % step["target"])
    parts.append(step["action"])
    if step["text"]:
        note = "(%s %dms)" % (step["text_helper"], step["text_latency_ms"]) if step["text_helper"] else ""
        parts.append("text=%r %s" % (step["text"], note.strip()))
    parts.append("changed=%s" % step["page_changed"])
    if step.get("success") is False:
        parts.append("failed=%s" % step.get("error"))
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
    calls = state.get("model_calls", decisions)
    if "model_calls" in state:
        line += " requests=%d" % len(calls)
    if not quiet and calls:
        latencies = [d.get("latency_ms", 0) for d in calls]
        label = "模型请求" if "model_calls" in state else "决策"
        line += "  %s均值 %dms" % (label, sum(latencies) // len(latencies))
        text_calls = state["text_calls"]
        if text_calls:
            text_ms = sum(t["latency_ms"] for t in text_calls)
            line += "  文本生成 %d 次均值 %dms" % (len(text_calls), text_ms // len(text_calls))
    if not quiet and state.get("round_timings"):
        rounds = state["round_timings"]
        line += "  累计模型 %.1fs / 设备阶段 %.1fs / 其他 %.1fs" % tuple(
            sum(r[k] for r in rounds) / 1000 for k in ("model_ms", "device_ms", "other_ms"))
    if not quiet:
        startup = sum(t["duration_ms"] for t in state.get("device_timings", [])
                      if t["phase"] == "startup" and t["stage"] == "observe_visual.total")
        if startup:
            line += "  初始化视觉观察 %.1fs（不含在 elapsed 中）" % (startup / 1000)
    return line


def format_tokens(state: Dict) -> str:
    usage = usage_totals(state)
    decision, text, total = usage["decision"], usage["text"], usage["total"]
    line = "tokens 决策 in≈%d out≈%d" % (decision["input_tokens"], decision["output_tokens"])
    if text["requests"]:
        line += " · 文本 in≈%d out≈%d" % (text["input_tokens"], text["output_tokens"])
    line += " · 合计 in≈%d out≈%d" % (total["input_tokens"], total["output_tokens"])
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
        "--no-screenshots",
        action="store_true",
        help="Skip fast-path screenshots; visual recovery still requires screenshots.",
    )
    parser.add_argument(
        "--action-interval",
        type=float,
        default=None,
        help="Extra seconds to wait after each executed action; overrides config.yaml.",
    )
    parser.add_argument("--quiet", action="store_true", help="One line per action, no decision details.")
    parser.add_argument("--no-vision-fallback", action="store_true", help="Disable screenshot-based recovery.")
    parser.add_argument("--vision-only", action="store_true", help="Use visual planning/execution from the first observation; no Jev key needed.")
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
    vision_only = args.vision_only or config["vision_only"]
    if not vision_only and not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY is missing; set it in the environment or .env.", file=sys.stderr)
        return 1

    record_dir = args.record_dir or config["record_dir"] or task_run_dir(task)
    interval = args.action_interval if args.action_interval is not None else config["action_interval"]
    quiet = args.quiet or config["quiet"]
    if args.no_screenshots:
        screenshots = False
    elif args.screenshots or config["screenshots"]:
        screenshots = True
    else:
        screenshots = None  # recording decides
    try:
        agent = Agent(
            task,
            adb_path=args.adb_path or config["adb_path"] or None,
            serial=args.device or config["device"],
            start_package=args.start_package or config["start_package"],
            record_dir=record_dir,
            screenshots=screenshots,
            action_interval=interval,
            vision_fallback=config["vision_fallback"] and not args.no_vision_fallback,
            vision_only=vision_only,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print("Cannot start run: %s" % error, file=sys.stderr)
        return 1
    last_step = 0
    last_event = 0
    def show_progress():
        nonlocal last_step, last_event
        state = agent.state
        lines = []
        if not quiet:
            for event in state["events"][last_event:]:
                lines.append((event.get("elapsed_ms", 0), 2 if event["type"] == "round_timing" else 0, format_event(event)))
        for step in state["history"][last_step:]:
            lines.append((step["elapsed_ms"], 1, format_step(step, quiet)))
        for _, _, line in sorted(lines, key=lambda item: item[:2]):
            print(line)
        last_event, last_step = len(state["events"]), len(state["history"])

    try:
        for _ in agent.run():
            show_progress()
    except (RuntimeError, ValueError) as error:
        show_progress()
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
    print(format_tokens(state))
    if not quiet:
        print(final_page_line(state))
    if state.get("answer"):
        print("answer: " + state["answer"])
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
