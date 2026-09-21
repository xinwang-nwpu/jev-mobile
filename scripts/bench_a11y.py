"""对比两种 A11Y 树获取方案在真机上的耗时。

    方案 A: Portal state_full   content query 一次往返 + 解析归一化（jev-mobile 当前快速路径）
    方案 B: uiautomator XML     dump/cat/rm 三次往返 + 解析（回退路径）

测量的是 agent 每步真实经历的完整链路（取数 + 解析 + 动作空间构建），交替执行以公平分布负载，
并拆分 shell 取数与 Python 解析的耗时占比。

用法（在 jev-mobile 目录下运行）:
    python scripts/bench_a11y.py --rounds 5
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_mobile.a11y import (  # noqa: E402
    _flatten,
    normalize_tree,
    parse_content_provider_output,
    parse_uiautomator_xml,
    snapshot_state,
)
from jev_mobile.device import Device  # noqa: E402
from jev_mobile.model import action_space  # noqa: E402

STATE_FULL_URI = "content://com.mobilerun.portal/state_full"


def bench_portal(device: Device, screen) -> dict:
    started = time.perf_counter()
    res = device._run("shell", "content", "query", "--uri", STATE_FULL_URI)
    cmd_ms = (time.perf_counter() - started) * 1000
    if res.returncode != 0 or not (res.stdout or "").strip():
        return {"ok": False, "error": "state_full query failed"}
    row = parse_content_provider_output(res.stdout)
    data = row.get("data") if isinstance(row, dict) else None
    if data is None and isinstance(row, dict) and row.get("status") == "success":
        data = row.get("result")
    if isinstance(data, str):
        data = json.loads(data)
    tree = data.get("a11y_tree") if isinstance(data, dict) else None
    if isinstance(tree, dict):
        tree = [tree]
    elements = normalize_tree(tree or [])
    state = snapshot_state(elements, {}, "mobilerun_portal", screen)
    node_count = len(_flatten(elements))
    total_ms = (time.perf_counter() - started) * 1000
    table, targets, _ = action_space(state["actions"])
    return {
        "ok": True,
        "cmd_ms": cmd_ms,
        "parse_ms": total_ms - cmd_ms,
        "total_ms": total_ms,
        "bytes": len(res.stdout or ""),
        "nodes": node_count,
        "elements": len(table),
        "click_targets": len(targets.get("CLICK", {})),
    }


def bench_uiautomator(device: Device, screen) -> dict:
    started = time.perf_counter()
    remote = "/sdcard/jev_bench_%d.xml" % int(time.time() * 1000)
    dump = device._run("shell", "uiautomator", "dump", remote)
    if dump.returncode != 0:
        return {"ok": False, "error": "uiautomator dump failed"}
    cat = device._run("shell", "cat", remote)
    device._run("shell", "rm", remote)
    cmd_ms = (time.perf_counter() - started) * 1000
    if cat.returncode != 0 or not (cat.stdout or "").strip().startswith("<"):
        return {"ok": False, "error": "empty uiautomator dump"}
    elements = parse_uiautomator_xml(cat.stdout)
    state = snapshot_state(elements, {}, "uiautomator", screen)
    node_count = len(_flatten(elements))
    total_ms = (time.perf_counter() - started) * 1000
    table, targets, _ = action_space(state["actions"])
    return {
        "ok": True,
        "cmd_ms": cmd_ms,
        "parse_ms": total_ms - cmd_ms,
        "total_ms": total_ms,
        "bytes": len(cat.stdout or ""),
        "nodes": node_count,
        "elements": len(table),
        "click_targets": len(targets.get("CLICK", {})),
    }


def report(name: str, runs: list) -> None:
    ok = [r for r in runs if r.get("ok")]
    if not ok:
        print("%-12s FAILED: %s" % (name, runs[0].get("error")))
        return
    totals = [r["total_ms"] for r in ok]
    cmds = [r["cmd_ms"] for r in ok]
    parses = [r["parse_ms"] for r in ok]
    last = ok[-1]
    print(
        "%-12s median=%.0fms  min=%.0f  max=%.0f  (取数 median=%.0fms / 解析 median=%.0fms)\n"
        "             payload=%.0fKB  节点=%d  元素表=%d  可点击目标=%d"
        % (
            name,
            statistics.median(totals),
            min(totals),
            max(totals),
            statistics.median(cmds),
            statistics.median(parses),
            last["bytes"] / 1024,
            last["nodes"],
            last["elements"],
            last["click_targets"],
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark portal state_full vs uiautomator XML.")
    parser.add_argument("--rounds", type=int, default=5, help="每条链路测几轮（默认 5）。")
    parser.add_argument("--device", default=None, help="adb 设备序列号；多台设备时必填。")
    args = parser.parse_args()

    device = Device(device=args.device)
    print("device=%s screen=%s rounds=%d" % (device.device or "default", device.screen, args.rounds))
    print("预热一轮...")
    bench_portal(device, device.screen)
    bench_uiautomator(device, device.screen)

    portal_runs, uia_runs = [], []
    for _ in range(args.rounds):
        portal_runs.append(bench_portal(device, device.screen))
        uia_runs.append(bench_uiautomator(device, device.screen))

    print()
    report("portal", portal_runs)
    report("uiautomator", uia_runs)
    ok_p = [r["total_ms"] for r in portal_runs if r.get("ok")]
    ok_u = [r["total_ms"] for r in uia_runs if r.get("ok")]
    if ok_p and ok_u:
        ratio = statistics.median(ok_u) / statistics.median(ok_p)
        print("\nuiautomator 是 portal 的 %.1f 倍耗时" % ratio)
    device.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
