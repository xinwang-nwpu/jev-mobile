"""交互式探测 TypeSafe Jev 模型的三种原语。

文档: https://docs.typesafe.ai   接口: POST /v1/systemone

  Choice  从选项中选一个          -> choice + probabilities + confidence
  Score   按等级量规打分          -> score(概率加权) + legend + confidence
  Noul    判断一句话是否成立      -> noul(0-1)

三种问题可以混在同一次请求里并行求值（--demo 演示这一点）。

用法（在 jev-mobile 目录下运行）:
    python scripts/jev_probe.py                                              # 交互模式
    python scripts/jev_probe.py --question "选一个" --options 爬山 逛街      # Choice
    python scripts/jev_probe.py --type score --question "严重吗" --options 轻 中 重
    python scripts/jev_probe.py --type noul --question "这是紧急求助吗" --state "消息原文..."
    python scripts/jev_probe.py --demo                                       # 一次请求发三种问题
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_mobile.model import post_json, validate_choice  # noqa: E402

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTION_TYPES = ("choice", "score", "noul")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def build_question(question_type: str, question: str, options=None) -> dict:
    options = list(options or [])
    if question_type == "choice":
        criteria = {str(i): option for i, option in enumerate(options, 1)}
        return {"type": "choice", "criteria": criteria, "instructions": {"goal": question, "rules": "Choose the single option that best answers the goal."}}
    if question_type == "score":
        if not 2 <= len(options) <= 10:
            raise ValueError("Score 需要 2-10 个等级（用 --options 逐个给出）")
        return {"type": "score", "instructions": question, "criteria": list(options)}
    return {"type": "noul", "instructions": question}


def ask(state: str, questions: dict, model: str):
    body = {"model": model, "state": state, "questions": questions}
    started = time.perf_counter()
    result = post_json(ENDPOINT, os.environ["TYPESAFE_API_KEY"], body)
    elapsed = round((time.perf_counter() - started) * 1000)
    return result, elapsed


def show_answer(question_id: str, question: dict, answer: dict) -> None:
    print("raw  : %s" % json.dumps(answer, ensure_ascii=False))
    if question["type"] == "choice":
        ids = question["criteria"]
        for name, prob in sorted(answer.get("probabilities", {}).items(), key=lambda kv: -kv[1]):
            marker = "   <-- 选中" if name == answer.get("choice") else ""
            print("  [%s] %-30s p=%.3f%s" % (name, ids.get(name, "?"), prob, marker))
        try:
            validate_choice(answer, set(ids))
            print("校验: 通过（分布合法且 choice 是 argmax，confidence=%.2f）" % answer.get("confidence", 0))
        except ValueError as error:
            print("校验: 失败 -> %s" % error)
    elif question["type"] == "score":
        legend = answer.get("legend", {})
        print("  score=%.2f  confidence=%.2f" % (answer.get("score", 0), answer.get("confidence", 0)))
        for name, prob in sorted(answer.get("probabilities", {}).items(), key=lambda kv: int(kv[0])):
            print("  等级%s p=%.3f  %s" % (name, prob, legend.get(name, "")))
    else:
        value = answer.get("noul", 0)
        print("  成立概率=%.3f  (是 %.1f%% / 否 %.1f%%)" % (value, value * 100, (1 - value) * 100))


def show(state: str, questions: dict, result: dict, elapsed: int) -> None:
    print("state=%r" % (state[:80] + ("..." if len(state) > 80 else "")))
    print("model=%s  latency=%dms  usage=%s" % (result.get("model"), elapsed, result.get("usage", {})))
    for question_id, question in questions.items():
        print("--- %s (%s)" % (question_id, question["type"]))
        show_answer(question_id, question, result.get("answers", {}).get(question_id, {}))


def read_lines(prompt: str) -> list:
    print(prompt)
    lines = []
    while True:
        try:
            line = input("  %s%d> " % (prompt.split("，")[0], len(lines) + 1)).strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return lines


def interactive(model: str) -> None:
    try:
        state = input("\n状态/背景（可空，空行继续）> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    while True:
        try:
            print("\n问题类型: 1=Choice 2=Score 3=Noul（空行退出）")
            pick = input("类型> ").strip()
            if not pick:
                break
            question_type = QUESTION_TYPES[int(pick) - 1]
            question = input("问题> ").strip()
            if not question:
                continue
            options = read_lines("选项/等级，每行一个，空行结束：") if question_type != "noul" else []
        except (EOFError, KeyboardInterrupt, IndexError, ValueError):
            print()
            break
        try:
            built = build_question(question_type, question, options)
            result, elapsed = ask(state, {"answer": built}, model)
        except (RuntimeError, ValueError) as error:
            print("失败: %s" % error)
            continue
        show(state, {"answer": built}, result, elapsed)


def demo(model: str) -> int:
    state = (
        "顾客来信：你们的导出按钮在我的 Safari 浏览器上一按就崩溃，Chrome 里是好的，"
        "但我们公司好几个同事只用 Safari，现在没人能导出报表。"
    )
    questions = {
        "severity": build_question("score", "这个问题对顾客的严重程度如何？", ["仅外观问题", "功能受损但有替代办法", "完全阻塞且无替代办法"]),
        "urgent": build_question("noul", "顾客是否在被一个阻塞工作流的问题困扰？"),
        "channel": build_question("choice", "下一步应该路由到哪个团队？", ["前端兼容性团队", "后端服务团队", "客服直接回复", "文档团队"]),
    }
    result, elapsed = ask(state, questions, model)
    show(state, questions, result, elapsed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the TypeSafe Jev model: Choice / Score / Noul.")
    parser.add_argument("--type", choices=QUESTION_TYPES, default="choice", help="Question type (default: choice).")
    parser.add_argument("--question", default=None, help="One-shot mode: the question to ask.")
    parser.add_argument("--options", nargs="+", default=None, help="Choices (choice) or levels (score).")
    parser.add_argument("--state", default="", help="State/context text sent with the question.")
    parser.add_argument("--demo", action="store_true", help="Send one request mixing all three question types.")
    parser.add_argument("--model", default=None, help="Defaults to TYPESAFE_MODEL or jev-latest.")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    load_env_file(Path.cwd() / ".env")
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY 缺失：复制 .env.example 为 .env 并填入。", file=sys.stderr)
        return 1
    model = args.model or os.environ.get("TYPESAFE_MODEL", "jev-latest")
    print("endpoint=%s  model=%s" % (ENDPOINT, model))

    if args.demo:
        return demo(model)
    if args.question:
        if args.type in ("choice", "score") and not args.options:
            print("--type %s 需要用 --options 提供选项/等级。" % args.type, file=sys.stderr)
            return 1
        try:
            question = build_question(args.type, args.question, args.options or [])
            result, elapsed = ask(args.state, {"answer": question}, model)
        except (RuntimeError, ValueError) as error:
            print("失败: %s" % error, file=sys.stderr)
            return 1
        show(args.state, {"answer": question}, result, elapsed)
        return 0
    interactive(model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
