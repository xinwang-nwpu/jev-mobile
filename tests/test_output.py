"""Output formatting: probabilities shown, hidden events surfaced."""

import os

from jev_mobile.__main__ import (
    format_event,
    format_probability_list,
    format_step,
    format_summary,
    format_tokens,
    task_run_dir,
)


def test_task_run_dir_sanitizes_for_windows():
    task = '打开 哔哩哔哩：播放"流沙"<视频>?'
    directory = task_run_dir(task)
    assert directory.startswith("runs" + os.sep)
    name = directory.rsplit(os.sep, 1)[-1]
    for bad in '<>:"/\\|?*':
        assert bad not in name
    assert " " not in name
    assert "打开_哔哩哔哩" in name


def test_task_run_dir_truncates_and_falls_back():
    assert len(task_run_dir("很" * 200).rsplit(os.sep, 1)[-1]) <= 60
    assert task_run_dir("？？？").rsplit(os.sep, 1)[-1] != ""
    assert task_run_dir("   ").rsplit(os.sep, 1)[-1] == "mobile_task"


def base_state():
    return {
        "status": "done",
        "elapsed_ms": 21400,
        "history": [{"step": 1}],
        "decisions": [
            {"latency_ms": 700, "usage": {"input_tokens": 400, "output_tokens": 30}},
            {"latency_ms": 900, "usage": {"input_tokens": 500, "output_tokens": 40}},
        ],
        "text_calls": [{"latency_ms": 2000, "usage": {"prompt_tokens": 50, "completion_tokens": 10}}],
        "page": {"app": "tv.danmaku.bili", "activity": "com.bilibili.ship.Player", "text": "在百万豪装录音棚听陶喆《流沙》\n第二行"},
    }


def test_probability_list_ranks_and_elides():
    text = format_probability_list({"CLICK": 0.2, "TYPE_TEXT": 0.5, "WAIT": 0.2, "DONE": 0.05, "BACK": 0.05})
    assert text.startswith("TYPE_TEXT 0.50 | CLICK 0.20")
    assert "+1" in text


def test_decision_event_line():
    line = format_event(
        {
            "type": "decision",
            "elapsed_ms": 22600,
            "operation": "TYPE_TEXT",
            "operation_probabilities": {"TYPE_TEXT": 0.9, "CLICK": 0.1},
            "confidence": 0.9,
            "latency_ms": 742,
            "target": "2",
            "target_probabilities": {"2": 0.88, "5": 0.12},
            "target_confidence": 0.88,
        }
    )
    assert "决策" in line and "TYPE_TEXT 0.90" in line and "conf=0.90" in line and "742ms" in line
    assert "目标: [2] 0.88 [5] 0.12" in line


def test_action_line_shows_text_helper_and_transition():
    line = format_step(
        {
            "elapsed_ms": 22600,
            "operation": "TYPE_TEXT",
            "target": "2",
            "action": "搜索查询",
            "text": "流沙",
            "text_helper": "glm-flash",
            "text_latency_ms": 830,
            "page_changed": True,
            "activity": "com.bilibili.search2.main.BiliMainSearchActivity",
            "activity_before": "tv.danmaku.bili.MainActivityV2",
        },
        quiet=False,
    )
    assert "text='流沙' (glm-flash 830ms)" in line
    assert "→ BiliMainSearchActivity" in line


def test_quiet_line_matches_old_format():
    line = format_step(
        {
            "elapsed_ms": 4100,
            "operation": "CLICK",
            "target": "14",
            "action": "哔哩哔哩",
            "text": None,
            "text_helper": None,
            "text_latency_ms": 0,
            "page_changed": True,
            "activity": "tv.danmaku.bili.MainActivityV2",
            "activity_before": "app.lawnchair.LawnchairLauncher",
        },
        quiet=True,
    )
    assert line == "[   4.1s] CLICK [14] 哔哩哔哩 changed=True"


def test_summary_reports_latency_and_text_calls():
    summary = format_summary(base_state(), quiet=False)
    assert "决策均值 800ms" in summary and "文本生成 1 次均值 2000ms" in summary
    assert "tokens" not in summary  # tokens moved to their own always-printed line


def test_tokens_line_aggregates_both_providers():
    line = format_tokens(base_state())
    # Decision usage uses input/output_tokens; the text helper reports prompt/completion.
    assert "决策 in≈900 out≈70" in line
    assert "文本 in≈50 out≈10" in line
    assert "合计 in≈950 out≈80" in line


def test_tokens_line_without_text_calls():
    state = base_state()
    state["text_calls"] = []
    line = format_tokens(state)
    assert "文本" not in line and "合计 in≈900 out≈70" in line


def test_hidden_events_have_lines():
    assert "DONE 被拒绝" in format_event({"type": "stale_done", "elapsed_ms": 1000})
    assert "判定卡住" in format_event({"type": "stuck", "elapsed_ms": 1000})
    assert "焦点窗口变化" in format_event({"type": "reobserve", "elapsed_ms": 1000, "reason": "focus_changed"})


def test_goal_gate_events_have_lines():
    assert "goal=0.12" in format_event({"type": "done_vetoed", "elapsed_ms": 1000, "goal_probability": 0.12})
    assert "goal=0.93" in format_event({"type": "goal_done", "elapsed_ms": 1000, "goal_probability": 0.93})


def test_decision_line_appends_goal_probability():
    line = format_event(
        {
            "type": "decision",
            "elapsed_ms": 22600,
            "operation": "CLICK",
            "operation_probabilities": {"CLICK": 0.9, "DONE": 0.1},
            "confidence": 0.9,
            "latency_ms": 742,
            "target": "2",
            "target_probabilities": {"2": 0.88, "5": 0.12},
            "target_confidence": 0.88,
            "goal_probability": 0.82,
        }
    )
    assert "goal=0.82" in line
