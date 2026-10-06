"""Offline trajectories for the visual planner/executor/reviewer and handoff."""

import base64
import copy
import io
import json
import subprocess

import pytest
from PIL import Image

import jev_mobile.agent as loop
from jev_mobile import vision, visual_images
from jev_mobile.a11y import fingerprint, snapshot_state
from jev_mobile.device import Device
from jev_mobile.__main__ import format_event, format_step
from helpers import FakeDevice, make_decision, node


def requirement(status="pending"):
    return {"id": "r1", "description": "Requested final state", "status": status,
            "evidence": "Visible final value" if status == "satisfied" else "", "steps": []}


def plan(status="continue", **changes):
    continuing = status == "continue"
    return {"status": status, "summary": "Observed progress", "reason": "Based on the current observation",
            "requirements": [requirement("satisfied" if status == "complete" else "pending")],
            "plan": ["Change the required control"] if continuing else [],
            "subgoal": "Tap the target" if continuing else "",
            "success_condition": "The target changes state" if continuing else "", "memory": [], "answer": "", **changes}


def review(status="confirmed"):
    return {"status": status, "summary": "Fresh screenshot reviewed", "reason": "Evidence checked",
            "requirements": [requirement("satisfied" if status == "confirmed" else "uncertain")], "answer": "Verified result"}


def action(operation="CLICK", **args):
    return {"status": "act", "summary": "Observed progress", "completed_steps": [],
            "action": operation, "reason": "Visible target", "expected_effect": "Required state changes", **args}


def execution(status="complete", **changes):
    return {"status": status, "summary": "Observed progress", "reason": "Current observation evidence",
            "completed_steps": [{"index": 1, "evidence": "Visible stage result", "steps": []}], **changes}


class VisualDevice(FakeDevice):
    visual_reads = 0

    def observe_visual(self):
        self.visual_reads += 1
        page = snapshot_state([], {"app": "com.example", "activity": "Main"}, "vision", self.screen)
        page.update(screenshot="image-%d" % self.visual_reads, fingerprint="visual-%d" % len(self.acts))
        return page

    def list_apps(self):
        return ["com.example.app"]


def enable(monkeypatch, outputs):
    monkeypatch.setenv("VISION_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("VISION_MODEL", "test-vision")
    monkeypatch.setenv("VISION_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    for role in ("PLANNER", "EXECUTOR", "VERIFIER"):
        monkeypatch.delenv("VISION_%s_REASONING" % role, raising=False)
    requests, queue = [], list(outputs)

    def post(url, key, body, **kwargs):
        requests.append(copy.deepcopy(body))
        output = queue.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, dict) and "choices" in output:
            return output
        return {"choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3}}

    monkeypatch.setattr(vision, "post_json", post)
    return requests


@pytest.mark.parametrize("output", [
    action(point=[-1, 500]), action(point=[1001, 500]), action(point=[True, 500]),
    action(point=[1.5, 500]), action(point=[500]),
    action("SWIPE", start=[500, 500], end=[500, 500]),
    action("TYPE_TEXT", text="hello", point=[500, 500]),
    action("OPEN_APP", package="com.unknown.app"), action("DONE"), action("SHELL"),
    action("WAIT", duration=float("nan")), action("LONG_PRESS", point=[500, 500], duration_ms=-1),
    action("CLICK_AREA", box=[100, 500, 200, 100]), [],
])
def test_invalid_commands_never_execute(output):
    with pytest.raises(ValueError):
        vision.parse_action(output, (1920, 1080), ["com.example.app"])


def test_coordinate_contract_and_focused_append():
    _, click, _ = vision.parse_action(action(point=[1000, 0]), (1920, 1080))
    assert click["center"] == [1919, 0]
    _, box, _ = vision.parse_action(action("CLICK_AREA", box=[400, 400, 600, 600]), (1920, 1080))
    assert box["center"] == [960, 540]
    _, fill, text = vision.parse_action(action("TYPE_TEXT", text=" new", current_text="old", clear=False), (1080, 1920))
    assert "center" not in fill and fill["value"] == "old" and text == "old new"


@pytest.mark.parametrize("trigger,steps,expected", [
    ("blocked", 1, "blocked"), ("model_error", 1, "model_error"),
    ("observation_error", 0, "observation_error"), ("no_targets", 0, "no_targets"),
    ("stuck", 3, "stuck"), ("cycle", 4, "cycle"),
    ("completion_conflict", 3, "completion_conflict"), ("execution_error", 1, "execution_error"),
])
def test_handoff_preserves_goal_and_full_history(monkeypatch, trigger, steps, expected):
    requests = enable(monkeypatch, [plan("complete"), review()])
    button = node(text="Open", clickable=True)
    trees = [[button]]
    if trigger == "cycle":
        trees = [[button], [button, node(text="Menu")], [button], [button, node(text="Menu")], [button]]
    elif trigger == "no_targets":
        trees = [[node(text="Canvas")]]
    device = VisualDevice(trees)
    if trigger == "execution_error":
        monkeypatch.setattr(device, "act", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Input failed")))
    if trigger == "observation_error":
        monkeypatch.setattr(device, "observe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("A11Y unavailable")))

    def fast(page, goal, history):
        if trigger == "model_error":
            raise ValueError("Malformed response")
        op = "BLOCKED" if trigger == "blocked" else "DONE" if trigger == "completion_conflict" else "CLICK"
        return make_decision(op, "1" if op == "CLICK" else None, page, goal={"satisfied": False, "probability": 0.1})

    monkeypatch.setattr(loop, "choose", fast)
    agent = loop.Agent("original goal", device=device, screenshots=False)
    for _ in range(steps):
        agent.tick()
    assert agent.state["mode"] == "vision" and agent.state["recovery_reason"] == expected
    history_size = len(agent.state["history"])
    reads = device.visual_reads
    agent.tick()
    assert agent.state["status"] == "done" and device.visual_reads > reads
    assert len(agent.state["history"]) == history_size
    assert sum(e["type"] == "handoff" for e in agent.state["events"]) == 1
    context = json.loads(requests[0]["messages"][1]["content"][0]["text"])
    assert context["goal"] == "original goal" and len(context["attempted_actions"]) == history_size
    assert context["handoff"]["reason"] == expected
    serialized = json.dumps(agent.trace())
    assert '"screenshot":' not in serialized and "data:image" not in serialized
    assert agent.trace()["decisions"][-1]["evidence"]
    assert agent.trace()["usage"]["decision"]["input_tokens"] == 24
    assert agent.state["answer"] == "Verified result"


def test_long_handoff_keeps_early_input_and_recent_page_evidence(monkeypatch):
    requests = enable(monkeypatch, [plan("complete"), review()])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("original goal", device=device)
    agent.state["history"] = [{"step": i + 1, "operation": "TYPE_TEXT", "text": "early value" if i == 0 else str(i),
                                "page_after": {"text": "Observed page %d" % i}} for i in range(14)]
    agent.tick()
    context = json.loads(requests[0]["messages"][1]["content"][0]["text"])
    assert context["attempted_actions"][0]["text"] == "early value"
    assert len(context["attempted_actions"]) == 14 and len(context["recent_outcomes"]) == 3
    assert context["recent_outcomes"][-1]["page_after"]["text"] == "Observed page 13"


def test_review_rejection_repairs_then_completes_with_memory(monkeypatch):
    fact = {"key": "required.value", "fact": "Required value found", "evidence": "Visible label", "steps": []}
    requests = enable(monkeypatch, [
        plan(memory=[fact]), action(point=[500, 500]),
        execution(), review("continue"),
        plan(memory=[fact]), action("TYPE_TEXT", text="new", current_text="old", clear=True),
        execution(), review(),
    ])
    monkeypatch.setenv("VISION_PLANNER_MODEL", "planner-model")
    monkeypatch.setenv("VISION_EXECUTOR_MODEL", "executor-model")
    monkeypatch.setenv("VISION_VERIFIER_MODEL", "review-model")
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("Set requested value", device=device, vision_only=True)
    agent.tick()
    assert agent.state["history"][0]["expected_effect"] == "Required state changes"
    agent.tick()
    assert agent.state["status"] == "ready" and len(device.acts) == 1
    agent.tick()
    assert len(device.acts) == 2 and device.acts[-1][1] == "new"
    agent.tick()
    assert agent.state["status"] == "done" and agent.state["progress"]["memory"] == [fact]
    context = json.loads(requests[4]["messages"][1]["content"][0]["text"])
    assert context["progress"]["feedback"]["kind"] == "completion_rejected"
    assert [r["model"] for r in requests[:4]] == ["planner-model", "executor-model", "executor-model", "review-model"]
    images = [p for p in requests[2]["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(images) == 2 and images[0] != images[1]
    assert "视觉 TYPE_TEXT" in format_step(agent.state["history"][-1], False)
    assert "完成复核" in format_event(next(e for e in agent.state["events"] if e["type"] == "visual_review"))


def test_invalid_executor_response_is_corrected_without_execution(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[5000, 1]), action(point=[500, 500])])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    assert len(device.acts) == 1 and len(agent.state["model_calls"]) == 3
    assert agent.state["model_calls"][1]["status"] == "error"
    assert "Correct this response error" in requests[2]["messages"][-1]["content"]
    assert agent.trace()["usage"]["decision"]["input_tokens"] == 36


def completion(content, finish="stop", reasoning=0):
    return {"model": "actual-model", "choices": [{"finish_reason": finish,
            "message": {"content": content, "reasoning_content": "Private reasoning is not persisted"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": reasoning + 3,
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}


@pytest.mark.parametrize("base,model,expected", [
    ("https://openrouter.ai/api/v1", "deepseek/deepseek-v4-flash",
     [{"reasoning": {"effort": "low"}}, {"reasoning": {"enabled": False}}, {"reasoning": {"effort": "low"}}]),
    ("https://api.deepseek.com/v1", "deepseek-v4-flash",
     [{"thinking": {"type": "enabled"}, "reasoning_effort": "low"}, {"thinking": {"type": "disabled"}},
      {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}]),
    ("https://gateway.example/v1", "deepseek-v4-flash",
     [{"thinking": {"type": "enabled"}, "reasoning_effort": "low"}, {"thinking": {"type": "disabled"}},
      {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}]),
    ("https://gateway.example/v1", "other-vision", [{}, {}, {}]),
])
def test_role_reasoning_defaults_and_provider_payloads(monkeypatch, base, model, expected):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500]), execution(), review()])
    monkeypatch.setenv("VISION_MODEL_BASE_URL", base)
    monkeypatch.setenv("VISION_MODEL", model)
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    agent.tick()
    agent.tick()
    for request, options in zip((requests[0], requests[1], requests[3]), expected):
        assert {k: request[k] for k in ("reasoning", "thinking", "reasoning_effort") if k in request} == options
    assert agent.state["status"] == "done"


def test_reasoning_overrides_and_invalid_setting_fail_before_spending(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500])])
    monkeypatch.setenv("VISION_PLANNER_REASONING", "default")
    monkeypatch.setenv("VISION_EXECUTOR_REASONING", "low")
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    agent.tick()
    assert "reasoning" not in requests[0] and requests[1]["reasoning"] == {"effort": "low"}
    monkeypatch.setenv("VISION_EXECUTOR_REASONING", "typo")
    with pytest.raises(RuntimeError, match="Visual reasoning must be"):
        agent.tick()
    assert len(requests) == len(agent.state["model_calls"]) == 2


@pytest.mark.parametrize("finish", ["length", None])
def test_reasoning_consumes_budget_then_disabled_retry_recovers(monkeypatch, finish):
    requests = enable(monkeypatch, [completion("", finish, 4095), completion(json.dumps(plan())),
                                    action(point=[500, 500])])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    assert len(device.acts) == 1 and len(requests) == 3
    assert requests[0]["reasoning"] == {"effort": "low"}
    assert requests[1]["reasoning"] == {"enabled": False}
    assert requests[0]["max_tokens"] == requests[1]["max_tokens"] == 4096
    calls = agent.trace()["model_calls"]
    assert calls[0]["reasoning_tokens"] == 4095 and calls[0]["retry_action"] == "disable_reasoning"
    assert calls[0]["finish_reason"] == finish and calls[0]["content_chars"] == 0
    assert calls[1]["response_model"] == "actual-model" and calls[1]["status"] == "ok"
    assert agent.trace()["usage"]["decision"]["output_tokens"] == 4104
    assert "Private reasoning" not in json.dumps(agent.trace())
    event = next(e for e in agent.state["events"] if e["type"] == "visual_retry")
    assert "关闭思考后重试" in format_event(event) and "4095" in format_event(event)
    assert not any(m["role"] == "assistant" for m in requests[1]["messages"])


@pytest.mark.parametrize("recovered", [True, False])
def test_even_valid_json_at_output_limit_cannot_execute_and_budget_only_grows_once(monkeypatch, recovered):
    raw = json.dumps(action(point=[500, 500]))
    requests = enable(monkeypatch, [plan(), completion(raw, "length"),
                                    completion(raw, "stop" if recovered else "length")])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    if recovered:
        agent.tick()
        assert len(device.acts) == 1
    else:
        with pytest.raises(RuntimeError, match="maximum output budget"):
            agent.tick()
        assert not device.acts and not agent.state["history"] and agent.state["status"] == "blocked"
    assert len(requests) == 3
    assert [r["max_tokens"] for r in requests[1:]] == [4096, 8192]
    assert agent.state["model_calls"][1]["retry_action"] == "increase_output_limit"
    assert not any(m["role"] == "assistant" for m in requests[2]["messages"])


@pytest.mark.parametrize("supported", [True, False])
def test_uncontrolled_reasoning_failure_stops_without_repeated_spending(monkeypatch, supported):
    outputs = [plan(), completion(None, "length", 4095)] if supported else [completion(None, "length", 4095)]
    requests = enable(monkeypatch, outputs)
    if not supported:
        monkeypatch.setenv("VISION_MODEL_BASE_URL", "https://other.example/v1")
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    with pytest.raises(RuntimeError, match="no compatible control"):
        agent.tick()
    assert len(requests) == len(outputs) and not device.acts
    call = agent.state["model_calls"][-1]
    assert call["retry_action"] == "stop" and call["reasoning_control"] == supported


@pytest.mark.parametrize("raw", [None, "", "  \n"])
def test_empty_body_without_reasoning_metadata_is_bounded_and_diagnosed(monkeypatch, raw):
    outputs = [plan()] + [completion(raw, None) for _ in range(3)]
    requests = enable(monkeypatch, outputs)
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    with pytest.raises(RuntimeError, match="empty_content; finish_reason=None"):
        agent.tick()
    assert len(requests) == 4 and not agent.state["history"]
    assert all(c["error_kind"] == "empty_content" for c in agent.state["model_calls"][1:])
    assert agent.state["model_calls"][-1]["retry_action"] == "stop"
    assert not any(m["role"] == "assistant" for m in requests[-1]["messages"])


@pytest.mark.parametrize("refusal", [False, True])
def test_provider_rejection_never_executes_or_retries(monkeypatch, refusal):
    response = completion(json.dumps(action(point=[500, 500])), "stop" if refusal else "content_filter")
    if refusal:
        response["choices"][0]["message"]["refusal"] = "Declined"
    requests = enable(monkeypatch, [plan(), response])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    with pytest.raises(RuntimeError, match="response_rejected"):
        agent.tick()
    assert len(requests) == 2 and not agent.state["history"]
    assert agent.state["model_calls"][-1]["retry_action"] == "stop"


def test_request_failure_is_distinct_from_json_repair(monkeypatch):
    requests = enable(monkeypatch, [RuntimeError("HTTP 503"), plan(), action(point=[500, 500])])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    agent.tick()
    assert len(requests) == 3 and len(agent.state["history"]) == 1
    assert agent.state["model_calls"][0]["error_kind"] == "request_error"
    assert agent.state["model_calls"][0]["retry_action"] == "retry_request"


def test_schema_repairs_and_failures_use_the_shared_request_budget(monkeypatch):
    requests = enable(monkeypatch, [{}] * 10)
    monkeypatch.setattr(loop, "MAX_STEPS", 1)  # four actual inference attempts
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        agent.tick()
    assert agent.state["status"] == "blocked" and len(requests) == 3 and not agent.state["history"]
    agent.state["status"] = "ready"
    with pytest.raises(RuntimeError, match="model-call budget"):
        agent.tick()
    assert len(requests) == 4 and len(agent.state["model_calls"]) == 4


def test_failure_outcome_reaches_planner_and_can_recover(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500]), plan(), action("BACK")])
    device = VisualDevice([[node(text="Canvas")]])
    original = device.act
    def flaky(action, text=None):
        if action["kind"] == "click":
            raise RuntimeError("Tap rejected")
        original(action, text)
    device.act = flaky
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    assert agent.state["history"][0]["success"] is False and agent.state["status"] == "ready"
    agent.tick()
    context = json.loads(requests[2]["messages"][1]["content"][0]["text"])
    assert context["recent_outcomes"][0]["error"] == "Tap rejected"
    assert context["progress"]["feedback"]["kind"] == "execution_error"
    assert device.acts[0][0]["keycode"] == 4


def test_stalled_visual_actions_replan_and_are_bounded(monkeypatch):
    tap = action(point=[500, 500])
    enable(monkeypatch, [plan(), tap, tap, tap, plan(), tap, plan(), tap])
    device = VisualDevice([[node(text="Canvas")]])
    def static():
        page = snapshot_state([], {"app": "com.example", "activity": "Main"}, "vision", device.screen)
        page.update(screenshot="image", fingerprint="same")
        return page
    device.observe_visual = static
    agent = loop.Agent("goal", device=device, vision_only=True)
    list(agent.run())
    assert agent.state["status"] == "blocked" and len(agent.state["history"]) == 5
    assert agent.state["progress"]["recoveries"] == vision.MAX_RECOVERIES


def test_checklist_cannot_drop_requirement_or_use_future_evidence():
    previous = [requirement()]
    with pytest.raises(ValueError, match="removed"):
        vision.parse_plan(plan(requirements=[{**requirement(), "id": "replacement"}]), 0, previous)
    with pytest.raises(ValueError, match="not occurred"):
        vision.parse_plan(plan("complete", requirements=[{**requirement("satisfied"), "steps": [1]}]), 0, [])
    with pytest.raises(ValueError, match="positive evidence"):
        vision.parse_review({**review(), "requirements": [requirement()]}, 1, previous)
    with pytest.raises(ValueError, match="verified findings"):
        vision.parse_review({**review(), "answer": ""}, 1, previous, "Claimed finding")


def test_saved_checklist_accepts_status_updates_and_ignores_reworded_descriptions():
    previous = [requirement(), {**requirement(), "id": "r2", "description": "Second requested result"}]
    original = copy.deepcopy(previous)
    updates = [{k: v for k, v in r.items() if k != "description"} for r in previous]
    updates[0].update(status="satisfied", evidence="Observed result", steps=[1])
    updates[1]["description"] = "Reworded or incorrect replacement"
    for parse, output in ((vision.parse_plan, plan(requirements=list(reversed(updates)))),
                          (vision.parse_review, {**review("continue"), "requirements": list(reversed(updates))})):
        raw = copy.deepcopy(output)
        parsed = parse(output, 1, previous)
        assert [r["description"] for r in parsed["requirements"]] == [r["description"] for r in previous]
        assert [r["id"] for r in parsed["requirements"]] == ["r1", "r2"]
        assert parsed["requirements"][0]["status"] == "satisfied"
        assert parsed["requirements"][0]["steps"] == [1]
        assert previous == original and output == raw


@pytest.mark.parametrize("ids", [[], ["r1"], ["r1", "other"], ["r1", "r2", "extra"], ["r1", "r1", "r2"]])
def test_planner_and_verifier_reject_missing_unknown_or_duplicate_saved_ids(ids):
    previous = [requirement(), {**requirement(), "id": "r2"}]
    updates = [{"id": identifier, "status": "pending", "evidence": "", "steps": []} for identifier in ids]
    for parse, output in ((vision.parse_plan, plan(requirements=updates)),
                          (vision.parse_review, {**review("continue"), "requirements": updates})):
        with pytest.raises(ValueError):
            parse(output, 0, previous)


def test_checklist_creation_and_status_only_updates_still_require_valid_evidence():
    update = {"id": "r1", "status": "satisfied", "evidence": "Visible result", "steps": []}
    with pytest.raises(ValueError, match="description"):
        vision.parse_plan(plan("complete", requirements=[update]), 0, [])
    for changes in ({"evidence": ""}, {"steps": [1]}, {"steps": [True]}, {"status": "done"}):
        invalid = [{**update, **changes}]
        for parse, output in ((vision.parse_plan, plan("complete", requirements=invalid)),
                              (vision.parse_review, {**review(), "requirements": invalid})):
            with pytest.raises(ValueError):
                parse(output, 0, [requirement()])


def test_multiturn_checklist_updates_complete_without_description_repairs(monkeypatch):
    satisfied = {"id": "r1", "status": "satisfied", "evidence": "Visible final value", "steps": [2]}
    requests = enable(monkeypatch, [
        plan(plan=["First stage", "Second stage"]), action(point=[500, 500]),
        action("BACK", completed_steps=[{"index": 1, "evidence": "First stage observed", "steps": [1]}]),
        execution(completed_steps=[{"index": 2, "evidence": "Second stage observed", "steps": [2]}]),
        {**review(), "requirements": [satisfied]},
    ])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    for _ in range(3):
        agent.tick()
    assert agent.state["status"] == "done" and len(agent.state["history"]) == 2
    assert len(requests) == 5 and not any(e["type"] == "visual_retry" for e in agent.state["events"])
    assert agent.state["progress"]["requirements"] == [{**satisfied, "description": requirement()["description"]}]
    context = json.loads(requests[-1]["messages"][1]["content"][0]["text"])
    assert context["progress"]["requirements"][0]["description"] == requirement()["description"]
    assert agent.trace()["decisions"][-1]["evidence"] == "Requested final state: Visible final value"


def test_six_actions_follow_one_plan_then_review_actual_before_and_after_images(monkeypatch):
    stages = ["Observed stage %d" % i for i in range(1, 7)]
    outputs = [plan(plan=stages)]
    for i in range(6):
        done = [{"index": i, "evidence": stages[i - 1], "steps": [i]}] if i else []
        outputs.append(action(point=[100 + i * 100, 500], completed_steps=done, summary="Reached stage %d" % (i + 1)))
    outputs += [execution(completed_steps=[{"index": 6, "evidence": stages[-1], "steps": [6]}]), review()]
    requests = enable(monkeypatch, outputs)
    device = VisualDevice([[node(text="Canvas")]])
    capture = device.observe_visual
    device.observe_visual = lambda: {**capture(), "app": "com.qq" if device.acts else "com.launcher"}
    agent = loop.Agent("goal", device=device, vision_only=True)
    list(agent.run())
    assert agent.state["status"] == "done" and len(device.acts) == 6
    roles = [c["role"] for c in agent.state["model_calls"]]
    assert roles == ["planner"] + ["executor"] * 7 + ["verifier"]
    assert len(requests) == 9 and agent.state["progress"]["revision"] == 1
    assert agent.state["progress"]["plan_cursor"] == 6
    assert [s["index"] for s in agent.state["progress"]["completed_steps"]] == list(range(1, 7))
    for i, request in enumerate(requests[1:7]):
        context = json.loads(request["messages"][1]["content"][0]["text"])
        assert len(context["recent_outcomes"]) == min(i, 2)
        assert "attempted_actions" not in context
        assert context["progress"]["plan_cursor"] == max(0, i - 1)
        assert context["progress"]["plan"] == stages
    image_urls = lambda r: [p["image_url"]["url"] for p in r["messages"][1]["content"] if p["type"] == "image_url"]
    assert image_urls(requests[-1])[0] == image_urls(requests[6])[-1]
    assert image_urls(requests[-1])[0] != image_urls(requests[-1])[-1]
    assert device.visual_reads == 9  # initial + one after planning + six outcomes + review
    assert "视觉进度 6/6" in format_event(next(e for e in agent.state["events"]
                                              if e["type"] == "visual_progress" and e["completed"] == 6))


@pytest.mark.parametrize("completed", [
    [{"index": 2, "evidence": "Skipped stage", "steps": []}],
    [{"index": True, "evidence": "Boolean", "steps": []}],
    [{"index": 1, "evidence": "", "steps": []}],
    [{"index": 1, "evidence": "Future action", "steps": [2]}],
    [{"index": 1, "evidence": "First", "steps": []}, {"index": 1, "evidence": "Repeated", "steps": []}],
])
def test_invalid_plan_advancement_is_repaired_without_mutating_progress(monkeypatch, completed):
    requests = enable(monkeypatch, [plan(plan=["First", "Second"]),
                                    action(point=[500, 500], completed_steps=completed), action(point=[500, 500])])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    agent.tick()
    assert len(requests) == 3 and len(agent.state["history"]) == 1
    assert agent.state["model_calls"][1]["status"] == "error"
    assert agent.state["progress"]["plan_cursor"] == 0 and not agent.state["progress"]["completed_steps"]


@pytest.mark.parametrize("invalid", [execution(completed_steps=[]), execution(action="CLICK", point=[500, 500]),
                                      action(point=[500, 500], completed_steps=execution()["completed_steps"])])
def test_incomplete_or_mixed_completion_cannot_act_or_skip_review(monkeypatch, invalid):
    requests = enable(monkeypatch, [plan()] + [invalid] * 3)
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        agent.tick()
    assert not device.acts and agent.state["progress"]["plan_cursor"] == 0
    assert len(requests) == 4 and not any(c["role"] == "verifier" for c in agent.state["model_calls"])


def test_executor_deviation_replans_remaining_work_with_original_checklist(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500]),
                                    execution("replan", completed_steps=[], reason="Unexpected welcome popup"),
                                    plan(plan=["Dismiss popup", "Continue original task"]), action("BACK")])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    original = copy.deepcopy(agent.state["progress"]["requirements"])
    agent.tick()
    assert len(device.acts) == 1 and agent.state["progress"]["needs_plan"]
    agent.tick()
    assert len(device.acts) == 2 and agent.state["progress"]["revision"] == 2
    context = json.loads(requests[3]["messages"][1]["content"][0]["text"])
    assert context["progress"]["feedback"]["kind"] == "plan_deviation"
    assert context["progress"]["requirements"] == original
    assert agent.state["progress"]["plan_cursor"] == 0


def test_partial_plan_exhaustion_requests_next_stage_and_replan_loops_are_bounded(monkeypatch):
    requests = enable(monkeypatch, [part for _ in range(3) for part in
                                    (plan(), execution("replan", reason="Discovery requires next stage"))])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    list(agent.run())
    assert agent.state["status"] == "blocked" and not device.acts
    assert len(requests) == 6 and agent.state["progress"]["recoveries"] == 3


def test_proposed_reading_answer_is_supplied_to_independent_verifier(monkeypatch):
    requests = enable(monkeypatch, [plan(), execution(answer="Observed value: 42"), review()])
    agent = loop.Agent("Read the value", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    agent.tick()
    assert agent.state["status"] == "done" and not agent.state["history"]
    context = json.loads(requests[-1]["messages"][1]["content"][0]["text"])
    assert context["progress"]["proposed_answer"] == "Observed value: 42"
    assert agent.state["answer"] == "Verified result"


def test_one_stage_can_take_multiple_actions_without_automatic_advancement(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[200, 500]), action(point=[700, 500]), execution(), review()])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    for _ in range(2):
        agent.tick()
        assert agent.state["history"][-1]["success"] and agent.state["history"][-1]["page_changed"]
        assert agent.state["progress"]["plan_cursor"] == 0
    agent.tick()
    assert agent.state["status"] == "done" and len(requests) == 5
    assert agent.state["progress"]["revision"] == 1


def test_unexpected_focus_change_invalidates_cached_plan_before_executor(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500]), plan(), action("BACK")])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    changes = iter([True])
    device.activity_changed = lambda page: next(changes, False)
    agent.tick()
    assert [c["role"] for c in agent.state["model_calls"]] == ["planner", "executor", "planner", "executor"]
    assert len(requests) == 4 and agent.state["progress"]["revision"] == 2
    assert any(e.get("reason") == "focus_changed" for e in agent.state["events"])


def test_role_contexts_keep_early_values_but_drop_redundant_audit_payload(monkeypatch):
    enable(monkeypatch, [])
    agent = loop.Agent("Keep the supplied recipient and message", device=IndexedVisualDevice([[node(text="Button", clickable=True)]]), vision_only=True)
    agent.state["history"] = [{"step": i + 1, "operation": "TYPE_TEXT" if i == 0 else "CLICK",
                               "text": "early required value" if i == 0 else None, "success": i != 0,
                               "page_after": {"text": "Observed %d" % i, "fingerprint": "audit", "image_file": "audit.png"},
                               "device_action": {"center": [1, 2], "label": "x" * 4000}} for i in range(14)]
    queries = []
    agent.visual.installed_apps = lambda: queries.append(True) or ["com.example.app"]
    planner, executor, verifier = [agent.visual.context(role) for role in ("planner", "executor", "verifier")]
    assert len(planner["attempted_actions"]) == len(verifier["attempted_actions"]) == 14
    assert len(executor["recent_outcomes"]) == 2 and "attempted_actions" not in executor
    assert executor["entered_values"][0]["text"] == "early required value"
    assert executor["entered_values"][0]["success"] is False
    assert "elements" in executor and "elements" not in planner and "elements" not in verifier
    assert "plan" not in verifier["progress"] and "installed_apps" not in verifier
    assert not queries and "audit.png" not in json.dumps([planner, executor, verifier])
    assert "device_action" not in json.dumps(executor)
    agent.state["progress"].update(plan=["Open target app"], subgoal="Open target app")
    assert agent.visual.context("executor")["installed_apps"] == ["com.example.app"]
    assert len(queries) == 1


def test_replanning_replaces_memory_snapshot_instead_of_accumulating_paraphrases(monkeypatch):
    old = {"key": "target.contact", "fact": "Target is Alice", "evidence": "Observed contact", "steps": []}
    stale = {**old, "key": "old.popup", "fact": "Popup was visible"}
    corrected = {**old, "fact": "The current target contact is Alice"}
    enable(monkeypatch, [plan(memory=[old, stale]), action(point=[500, 500]),
                         execution("replan", completed_steps=[]), plan(memory=[corrected]), action("BACK")])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    for _ in range(3):
        agent.tick()
    assert agent.state["progress"]["memory"] == [corrected]
    assert agent.trace()["model_calls"][0]["output"]["memory"] == [old, stale]
    duplicate_text = {**corrected, "key": "another.key", "fact": " THE current  target CONTACT is Alice "}
    assert len(vision.parse_plan(plan(memory=[corrected, duplicate_text]), 0, [])['memory']) == 1
    for invalid in ([old, old], [{k: v for k, v in old.items() if k != "key"}], [{**old, "steps": [99]}]):
        with pytest.raises(ValueError):
            vision.parse_plan(plan(memory=invalid), 0, [])


def test_action_budget_survives_handoff(monkeypatch):
    enable(monkeypatch, [plan(), action("TYPE_TEXT", text="new", current_text="old", clear=True), action(point=[700, 500])])
    monkeypatch.setattr(loop, "MAX_STEPS", 2)
    calls = iter(["CLICK", "BLOCKED"])
    monkeypatch.setattr(loop, "choose", lambda p, g, h: make_decision(op := next(calls), "1" if op == "CLICK" else None, p))
    device = VisualDevice([[node(text="Next", clickable=True)]])
    agent = loop.Agent("goal", device=device)
    agent.tick()
    agent.tick()
    agent.tick()
    with pytest.raises(ValueError, match="action budget"):
        agent.tick()
    assert len(device.acts) == 2 and agent.state["status"] == "blocked"


def test_fast_success_does_not_call_visual_roles(monkeypatch):
    requests = enable(monkeypatch, [])
    monkeypatch.setattr(loop, "choose", lambda p, g, h: make_decision("DONE"))
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Done", clickable=True)]]))
    list(agent.run())
    assert requests == [] and agent.state["mode"] == "fast"


def png(size=(1920, 1080), color=(25, 30, 35)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_size_grid_and_noise_threshold(monkeypatch):
    monkeypatch.setenv("VISION_IMAGE_MAX_SIDE", "960")
    page = visual_images.prepare(png())
    assert page["screen"] == [1920, 1080] and page["model_screen"] == [960, 540]
    with Image.open(io.BytesIO(base64.b64decode(page["screenshot"]))) as rendered:
        assert list(rendered.size) == page["model_screen"]
        assert rendered.getpixel((480, 400)) != (25, 30, 35)
    assert not visual_images.changed(page, visual_images.prepare(png(color=(26, 31, 36))))
    assert visual_images.changed(page, visual_images.prepare(png(color=(150, 150, 150))))
    assert visual_images.changed(page, visual_images.prepare(png(size=(1080, 1920))))


def test_device_visual_input_is_independent_of_a11y_and_checks_adb(monkeypatch):
    device = Device.__new__(Device)
    monkeypatch.setattr(device, "_screencap_bytes", png)
    monkeypatch.setattr(device, "_focus_app_activity", lambda: ("com.example", "Main"))
    tree_reads = []
    def unavailable(attempts):
        tree_reads.append(attempts)
        raise RuntimeError("A11Y unavailable")
    monkeypatch.setattr(device, "_read_tree", unavailable)
    monkeypatch.setattr("jev_mobile.device.time.sleep", lambda _: None)
    page = device.observe_visual()
    assert page["screen"] == [1920, 1080]
    assert page["a11y_fingerprint"] is None and "unavailable" in page["a11y_error"]
    device.observe_visual()
    assert tree_reads == [1]  # A failed optional probe does not delay every later screenshot.
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(device, "_run", run)
    monkeypatch.setattr(device, "_send_text", lambda text, delete: calls.append((text, delete)))
    device.act({"kind": "fill", "value": "old"}, text="new")
    device.act({"kind": "long_press", "center": [100, 200], "duration_ms": 950})
    device.act({"kind": "swipe", "start": [100, 200], "end": [300, 400], "duration_ms": 800})
    assert ("new", 3) in calls and not any(c[:3] == ("shell", "input", "tap") for c in calls)
    assert ("shell", "input", "swipe", 100, 200, 100, 200, 950) in calls
    assert ("shell", "input", "swipe", 100, 200, 300, 400, 800) in calls
    monkeypatch.setattr(device, "_run", lambda *a: subprocess.CompletedProcess(a, 1, "", ""))
    with pytest.raises(RuntimeError, match="ADB operation failed"):
        device.act({"kind": "click", "center": [10, 20]})


def test_adb_timeout_is_recoverable(monkeypatch):
    device = Device.__new__(Device)
    device.adb_path, device.device = "adb", None
    def timed_out(cmd, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(RuntimeError, match="timed out"):
        device._run("shell", "input", "tap", 1, 2)


def test_rejected_completion_cannot_escape_recovery_budget(monkeypatch):
    enable(monkeypatch, [part for _ in range(3) for part in (plan("complete"), review("continue"))])
    device = VisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    list(agent.run())
    assert agent.state["status"] == "blocked" and not device.acts
    assert len(agent.state["model_calls"]) == 6
    assert agent.state["progress"]["recoveries"] == 3


def test_images_are_unique_and_history_references_saved_pages(monkeypatch, tmp_path):
    enable(monkeypatch, [plan(), action(point=[500, 500]), execution(), review()])
    device = VisualDevice([[node(text="Canvas")]])
    original = device.observe_visual
    def capture():
        return {**original(), "screenshot": base64.b64encode(png()).decode("ascii")}
    device.observe_visual = capture
    agent = loop.Agent("goal", device=device, vision_only=True, record_dir=str(tmp_path))
    list(agent.run())
    before, after = (agent.state["history"][0][key]["image_file"] for key in ("page_before", "page_after"))
    assert before != after and (tmp_path / before).exists() and (tmp_path / after).exists()
    assert len(list(tmp_path.glob("*.png"))) == 4


def test_visual_device_start_does_not_probe_or_launch_portal(monkeypatch):
    monkeypatch.setattr(Device, "_read_screen", lambda _: (1080, 1920))
    monkeypatch.setattr(Device, "_ensure_portal_started", lambda _: pytest.fail("Unexpected Portal startup"))
    device = Device(prepare_portal=False)
    assert device.screen == (1080, 1920)


def test_cli_visual_only_runs_without_jev_credentials(monkeypatch, tmp_path, capsys):
    from jev_mobile import __main__ as cli
    enable(monkeypatch, [plan("complete"), review()])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_env_file", lambda: None)
    config = tmp_path / "task.yaml"
    config.write_text("task: inspect current state\nvision_only: true\n", encoding="utf-8")
    original = cli.Agent
    device = VisualDevice([[node(text="Canvas")]])
    capture = device.observe_visual
    device.observe_visual = lambda: {**capture(), "screenshot": base64.b64encode(png()).decode("ascii")}
    monkeypatch.setattr(cli, "Agent", lambda *a, **k: original(*a, device=device, **k))
    assert cli.main(["--config", str(config), "--record-dir", str(tmp_path / "run"), "--quiet"]) == 0
    output = capsys.readouterr().out
    assert "requests=2" in output and "Verified result" in output
    trace = json.loads((tmp_path / "run" / "trace.json").read_text(encoding="utf-8"))
    assert trace["mode"] == "vision" and trace["status"] == "done"


def test_cli_missing_visual_credentials_fails_before_device_or_trace(monkeypatch, tmp_path, capsys):
    from jev_mobile import __main__ as cli
    monkeypatch.setattr(cli, "load_env_file", lambda: None)
    monkeypatch.delenv("VISION_MODEL_API_KEY", raising=False)
    config = tmp_path / "task.yaml"
    config.write_text("task: inspect\n", encoding="utf-8")
    monkeypatch.setattr(loop, "Device", lambda *a, **k: pytest.fail("Device must not be started"))
    assert cli.main(["--config", str(config), "--vision-only", "--record-dir", str(tmp_path / "run")]) == 1
    assert "Cannot start run" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_executor_gets_an_image_captured_after_planning(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500])])
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    agent.tick()
    current_images = [[p["image_url"]["url"] for p in r["messages"][1]["content"] if p["type"] == "image_url"][-1]
                      for r in requests]
    assert current_images == ["data:image/png;base64,image-1", "data:image/png;base64,image-2"]


@pytest.mark.parametrize("changed", ["activity", "screen"])
def test_change_during_planning_discards_action_and_replans(monkeypatch, changed):
    requests = enable(monkeypatch, [plan()])
    device = VisualDevice([[node(text="Canvas")]])
    capture = device.observe_visual
    def moved():
        page = capture()
        if device.visual_reads > 1:
            page[changed] = "Other" if changed == "activity" else [1920, 1080]
        return page
    device.observe_visual = moved
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    assert agent.state["status"] == "ready" and not device.acts and len(requests) == 1
    assert any(e.get("reason") == "visual_plan_stale" for e in agent.state["events"])


def indexed_page(tree, screen=(1080, 2340)):
    page = snapshot_state(tree, {"app": "com.example", "activity": "Main"}, "vision", screen)
    page.update(a11y_fingerprint=fingerprint({**page, "text": ""}), a11y_source="test", a11y_error=None)
    return page


class IndexedVisualDevice(VisualDevice, Device):
    fresh_index = Device.fresh_index

    def _focus_app_activity(self):
        return "com.example", "Main"

    def _index_window(self):
        return "com.example", "Main", tuple(self.screen)

    def _read_tree(self, attempts=1):
        return self.trees[min(len(self.acts), len(self.trees) - 1)], {}, "test"

    def observe_visual(self):
        pixels = super().observe_visual()
        page = indexed_page(self.trees[min(len(self.acts), len(self.trees) - 1)], self.screen)
        return {**pixels, **page, "fingerprint": pixels["fingerprint"]}


def test_indexed_targets_share_jev_numbers_and_use_native_bounds():
    page = indexed_page([node(cls="android.widget.EditText", text="old", clickable=True)])
    elements = vision.visual_elements(page)
    assert len(elements) == 1 and elements[0]["index"] == 1
    assert elements[0]["operations"] == ["CLICK", "LONG_PRESS", "TYPE_TEXT"]
    assert elements[0]["bounds"] == [0, 43, 371, 86]
    for operation in ("CLICK", "LONG_PRESS"):
        _, target, _ = vision.parse_action(action(operation, index=1), page["screen"], page=page)
        assert target["center"] == [200, 150] and target["index"] == 1
        assert target["a11y_fingerprint"] == page["a11y_fingerprint"]
    _, target, text = vision.parse_action(action("TYPE_TEXT", index=1, text=" new", clear=False), page["screen"], page=page)
    assert target["value"] == "old" and text == "old new"
    with pytest.raises(ValueError, match="contradicts"):
        vision.parse_action(action("TYPE_TEXT", index=1, text="new", clear=True, current_text="wrong"), page["screen"], page=page)


@pytest.mark.parametrize("output", [
    action(index=0), action(index=-1), action(index=True), action(index=1.5),
    action(index="1"), action(index=None), action(index=99),
    action(index=1, point=[500, 500]), action("SWIPE", index=1),
    action("TYPE_TEXT", index=1, text="new", clear=True),
])
def test_invalid_indexes_or_operation_mismatch_cannot_resolve(output):
    page = indexed_page([node(text="Button", clickable=True)])
    with pytest.raises(ValueError):
        vision.parse_action(output, page["screen"], page=page)


def test_indexes_cannot_be_taken_from_unavailable_or_old_table():
    old = indexed_page([node(text="Button", clickable=True)])
    current = {**old, "a11y_fingerprint": None, "a11y_error": "Tree unavailable"}
    assert not vision.visual_elements(current)
    with pytest.raises(ValueError, match="No current"):
        vision.parse_action(action(index=1), current["screen"], page=current)
    _, coordinate, _ = vision.parse_action(action(point=[500, 500]), current["screen"], page=current)
    assert "index" not in coordinate


def test_indexed_input_only_accepts_editable_targets_even_without_click_flag():
    page = indexed_page([node(cls="android.widget.EditText", text="old", clickable=False)])
    _, target, text = vision.parse_action(action("TYPE_TEXT", index=1, text="new", clear=True), page["screen"], page=page)
    assert target["center"] == [200, 150] and text == "new"
    _, target, _ = vision.parse_action(action(index=1), page["screen"], page=page)
    assert target["kind"] == "click"


@pytest.mark.parametrize("operation,args", [
    ("CLICK", {}), ("LONG_PRESS", {"duration_ms": 950}),
    ("TYPE_TEXT", {"text": "new", "clear": True}),
])
def test_visual_roles_execute_indexed_actions_and_record_grounding(monkeypatch, operation, args):
    requests = enable(monkeypatch, [plan(), action(operation, index=1, **args)])
    device = IndexedVisualDevice([[node(cls="android.widget.EditText", text="old", clickable=True)]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    assert len(device.acts) == 1 and device.acts[0][0]["center"] == [200, 150]
    context = json.loads(requests[1]["messages"][1]["content"][0]["text"])
    assert context["elements"][0]["index"] == 1
    assert agent.state["history"][0]["target"] == 1
    assert "[1]" in format_step(agent.state["history"][0], False)
    assert agent.trace()["model_calls"][-1]["observed"]["elements"] == context["elements"]


def test_indexed_text_focuses_and_types_in_one_recorded_device_action(monkeypatch):
    requests = enable(monkeypatch, [plan(plan=["Enter the greeting"], subgoal="Enter 你好", success_condition="Input shows 你好"),
                                    action("TYPE_TEXT", index=1, text="你好", clear=True), execution(), review()])
    device = IndexedVisualDevice([[node(cls="android.widget.EditText", text="", clickable=True)],
                                  [node(cls="android.widget.EditText", text="你好", clickable=True)]])
    operations = []
    monkeypatch.setattr(device, "_tap", lambda center: operations.append(("focus", center)))
    monkeypatch.setattr(device, "_wait_keyboard", lambda: operations.append(("keyboard",)))
    monkeypatch.setattr(device, "_send_text", lambda text, delete: operations.append(("text", text, delete)))
    monkeypatch.setattr("jev_mobile.device.time.sleep", lambda _: None)
    original = device.act
    def execute(action, text=None):
        Device.act(device, action, text)
        original(action, text)
    device.act = execute
    agent = loop.Agent("Enter 你好", device=device, vision_only=True)
    list(agent.run())
    assert agent.state["status"] == "done" and len(agent.state["history"]) == 1
    assert agent.state["history"][0]["operation"] == "TYPE_TEXT"
    assert operations == [("focus", [200, 150]), ("keyboard",), ("text", "你好", 0)]
    assert len(requests) == 4 and not any(h["operation"] == "CLICK" for h in agent.state["history"])


def test_stale_index_is_discarded_before_tap_and_next_turn_can_use_coordinates(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(index=1), plan(), action(point=[500, 500])])
    device = IndexedVisualDevice([[node(text="First", clickable=True)]])
    post = vision.post_json
    def changing(url, key, body, **kwargs):
        result = post(url, key, body, **kwargs)
        if len(requests) == 2:
            device.trees = [[node(text="Different control", clickable=True)]]
        return result
    monkeypatch.setattr(vision, "post_json", changing)
    agent = loop.Agent("goal", device=device, vision_only=True)
    agent.tick()
    assert not device.acts and not agent.state["history"] and agent.state["status"] == "ready"
    assert any(e.get("reason") == "a11y_index_stale" for e in agent.state["events"])
    agent.tick()
    assert len(device.acts) == 1 and "index" not in device.acts[0][0]


def test_device_attaches_current_a11y_and_guard_ignores_pixel_animation(monkeypatch):
    device = Device.__new__(Device)
    tree = [node(text="Button", clickable=True), node(text="Clock")]
    monkeypatch.setattr(device, "_read_tree", lambda attempts: (tree, {}, "portal"))
    monkeypatch.setattr(device, "_screencap_bytes", png)
    monkeypatch.setattr(device, "_focus_app_activity", lambda: ("com.example", "Main"))
    monkeypatch.setattr(device, "_index_window", lambda: ("com.example", "Main", tuple(device.screen)))
    page = device.observe_visual()
    assert page["a11y_source"] == "portal" and vision.visual_elements(page)[0]["index"] == 1
    monkeypatch.setattr(device, "_screencap_bytes", lambda: pytest.fail("Light guard must not screenshot"))
    tree[1]["text"] = "Clock changed"
    assert device.fresh_index(page)
    tree[:] = [node(text="Button", clickable=True, bounds="[400,100][800,200]")]
    assert not device.fresh_index(page)
    tree[:] = [node(text="Button", clickable=True), node(text="Clock")]
    monkeypatch.setattr(device, "_index_window", lambda: ("com.example", "Main", (1080, 1920)))
    assert not device.fresh_index(page)


def test_index_guard_checks_window_again_after_tree_and_keeps_safe_fallback(monkeypatch):
    device = Device.__new__(Device)
    tree = [node(text="Button", clickable=True)]
    page = indexed_page(tree)
    window = ("com.example", "Main", tuple(page["screen"]))
    windows = iter([window, ("com.other", "Other", window[2])])
    monkeypatch.setattr(device, "_index_window", lambda: next(windows))
    monkeypatch.setattr(device, "_read_tree", lambda attempts: (tree, {}, "test"))
    monkeypatch.setattr(device, "observe_visual", lambda: pytest.fail("Unexpected full observation"))
    assert not device.fresh_index(page)
    monkeypatch.setattr(device, "_index_window", lambda: None)
    monkeypatch.setattr(device, "observe_visual", lambda: page)
    assert device.fresh_index(page)
    assert any(t["stage"] == "guard.index.fallback" for t in device.timings)
    monkeypatch.setattr(device, "_index_window", lambda: window)
    def failed_tree(attempts):
        raise RuntimeError("Tree unavailable")
    monkeypatch.setattr(device, "_read_tree", failed_tree)
    assert not device.fresh_index(page)
    assert any(t["stage"] == "guard.index.a11y" and not t["success"] for t in device.timings)


def test_device_stage_timings_survive_failed_input_and_trace_collection(monkeypatch):
    enable(monkeypatch, [])
    device = IndexedVisualDevice([[node(text="Canvas")]])
    agent = loop.Agent("goal", device=device, vision_only=True)
    monkeypatch.setattr(device, "_tap", lambda center: None)
    monkeypatch.setattr(device, "_wait_keyboard", lambda: None)
    def failed_text(*args, **kwargs):
        raise RuntimeError("Input failed")
    monkeypatch.setattr(device, "_send_text", failed_text)
    with pytest.raises(RuntimeError, match="Input failed"):
        Device.act(device, {"kind": "fill", "center": [10, 20]}, "text")
    trace = agent.trace()
    stages = {t["stage"]: t for t in trace["device_timings"]}
    assert not stages["input.text"]["success"] and not stages["action.total"]["success"]
    assert stages["input.focus"]["success"] and stages["input.keyboard_wait"]["success"]
    assert all(t["duration_ms"] >= 0 and "started_at" not in t for t in trace["device_timings"])
    assert not device.timings and len(agent.trace()["device_timings"]) == len(trace["device_timings"])


def test_round_timings_measure_all_models_devices_and_completion_with_a_controlled_clock(monkeypatch):
    requests = enable(monkeypatch, [plan(), action(point=[500, 500]), execution(), review()])
    clock = {"now": 100.0}
    monkeypatch.setattr(loop.time, "perf_counter", lambda: clock["now"])
    post = vision.post_json
    def slow_post(*args, **kwargs):
        clock["now"] += 2
        return post(*args, **kwargs)
    monkeypatch.setattr(vision, "post_json", slow_post)
    device = IndexedVisualDevice([[node(text="Canvas")]])
    capture, act = device.observe_visual, device.act
    def slow_capture():
        with device._timed("observe_visual.total"):
            clock["now"] += 0.5
            return capture()
    def slow_act(action, text=None):
        with device._timed("action.total"):
            clock["now"] += 1
            act(action, text)
    device.observe_visual, device.act = slow_capture, slow_act
    agent = loop.Agent("goal", device=device, vision_only=True)
    list(agent.run())
    first, last = agent.trace()["round_timings"]
    assert (first["duration_ms"], first["model_ms"], first["device_ms"], first["other_ms"]) == (6000, 4000, 2000, 0)
    assert (last["duration_ms"], last["model_ms"], last["device_ms"], last["other_ms"]) == (4500, 4000, 500, 0)
    assert first["requests"] == last["requests"] == 2 and last["actions"] == 0
    assert agent.state["elapsed_ms"] == 10500 and len(requests) == 4
    assert agent.trace()["model_calls"][-1]["request_bytes"] > 0
    assert agent.trace()["model_calls"][-1]["endpoint_host"] == "openrouter.ai"
    assert any(t["phase"] == "startup" for t in agent.trace()["device_timings"])
    assert "总耗时 6000ms" in format_event({"type": "round_timing", **first})
    assert "复核器请求" in format_event(next(e for e in agent.state["events"]
                                             if e["type"] == "visual_decision" and e["operation"] == "DONE"))


def test_failed_round_preserves_retry_time_and_final_elapsed(monkeypatch):
    enable(monkeypatch, [])
    clock = {"now": 100.0}
    monkeypatch.setattr(loop.time, "perf_counter", lambda: clock["now"])
    def failed_post(*args, **kwargs):
        clock["now"] += 2
        raise RuntimeError("HTTP 503")
    monkeypatch.setattr(vision, "post_json", failed_post)
    agent = loop.Agent("goal", device=VisualDevice([[node(text="Canvas")]]), vision_only=True)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        agent.tick()
    timing = agent.trace()["round_timings"][-1]
    assert timing["requests"] == 3 and timing["status"] == "blocked"
    assert timing["duration_ms"] == timing["model_ms"] == agent.state["elapsed_ms"] == 6000
    assert timing["actions"] == 0


def test_cli_flushes_failure_diagnostics_and_round_timing_before_exit(monkeypatch, tmp_path, capsys):
    from jev_mobile import __main__ as cli
    enable(monkeypatch, [RuntimeError("HTTP 503")] * 3)
    monkeypatch.setattr(cli, "load_env_file", lambda: None)
    config = tmp_path / "task.yaml"
    config.write_text("task: inspect\nvision_only: true\n", encoding="utf-8")
    original = cli.Agent
    device = VisualDevice([[node(text="Canvas")]])
    capture = device.observe_visual
    device.observe_visual = lambda: {**capture(), "screenshot": base64.b64encode(png()).decode("ascii")}
    monkeypatch.setattr(cli, "Agent", lambda *a, **k: original(*a, device=device, **k))
    assert cli.main(["--config", str(config), "--record-dir", str(tmp_path / "run")]) == 1
    output = capsys.readouterr()
    assert output.out.count("请求失败") == 3 and "本轮 1" in output.out
    assert "Run stopped" in output.err
    saved = json.loads((tmp_path / "run" / "trace.json").read_text(encoding="utf-8"))
    assert saved["round_timings"][0]["requests"] == 3 and saved["settings"]["vision_only"]


def test_optional_tree_probe_does_not_run_slow_recovery(monkeypatch):
    device = Device.__new__(Device)
    device._portal_usable = False
    calls = []
    def empty(command):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "empty dump", "")
    monkeypatch.setattr(device, "_shell", empty)
    with pytest.raises(RuntimeError, match="Optional A11Y"):
        device._read_tree(attempts=1)
    assert calls == ["uiautomator dump /dev/tty"]
