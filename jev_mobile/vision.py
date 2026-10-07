"""Independent visual workflow: plan, ground, execute feedback, review completion.

All persistent task data belongs to the parent Agent's shared state. This module
never executes a device command, and model output is validated before dispatch.
"""

import json
import math
import os
import re
import time
from urllib.parse import urlsplit

from .a11y import parse_bounds
from .model import action_space, post_json
from .visual_prompts import PLANNER, EXECUTOR, VERIFIER

MAX_REPAIRS = 3
MAX_RECOVERIES = 3
OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 8192


def reasoning_options(url, model, effort):
    """Only send provider-specific controls to a recognized endpoint/model."""
    if effort not in {"none", "low", "medium", "high", "max", "default"}:
        raise ValueError("Visual reasoning must be none/low/medium/high/max/default")
    if effort == "default":
        return {}
    host = urlsplit(url).hostname
    if host == "openrouter.ai":
        return {"reasoning": {"enabled": False} if effort == "none" else {"effort": effort}}
    if host == "api.deepseek.com" or model.rsplit("/", 1)[-1].lower().startswith("deepseek"):
        return ({"thinking": {"type": "disabled"}} if effort == "none" else
                {"thinking": {"type": "enabled"}, "reasoning_effort": "high" if effort == "medium" else effort})
    return {}


def configured():
    return bool(os.environ.get("VISION_MODEL_API_KEY") and os.environ.get("VISION_MODEL"))


def initial_progress():
    return {"summary": "", "requirements": [], "plan": [], "subgoal": "", "success_condition": "",
            "memory": [], "feedback": {}, "recoveries": 0, "revision": 0,
            "needs_plan": True, "proposed_answer": ""}


def page_summary(page):
    if not page:
        return None
    return {**{k: page.get(k) for k in ("app", "activity", "source", "screen", "model_screen", "fingerprint", "image_file",
                                     "a11y_source", "a11y_fingerprint", "a11y_error")},
            "text": (page.get("text") or "")[:1500]}


def visual_elements(page):
    """The same numbers as Jev, with bounds in the image's normalized space."""
    if not page or not page.get("a11y_fingerprint"):
        return []
    rows, targets, _ = action_space(page["actions"])
    elements = []
    for row in rows:
        target = targets.get("CLICK", {}).get(row["index"]) or targets["TYPE_TEXT"][row["index"]]
        bounds = parse_bounds(target["bounds"])
        normalized = [min(1000, max(0, round(n * 1000 / (page["screen"][axis % 2] - 1))))
                      for axis, n in enumerate(bounds)]
        operations = ["CLICK", "LONG_PRESS"] + (["TYPE_TEXT"] if "TYPE_TEXT" in row["operations"] else [])
        elements.append({**row, "index": int(row["index"]), "bounds": normalized, "operations": operations})
    return elements


def indexed_target(page, index, operation):
    if type(index) is not int or index < 1:
        raise ValueError("Element index must be a positive integer")
    if not page or not page.get("a11y_fingerprint"):
        raise ValueError("No current A11Y indexes are available; use coordinates or focused input")
    _, targets, _ = action_space(page["actions"])
    candidates = (targets.get("TYPE_TEXT", {}) if operation == "TYPE_TEXT"
                  else {**targets.get("TYPE_TEXT", {}), **targets.get("CLICK", {})})
    target = candidates.get(str(index))
    if target is None:
        raise ValueError("Index is missing or cannot perform this operation in the current elements table")
    if any(not 0 <= n < size for n, size in zip(target["center"], page["screen"])):
        raise ValueError("Indexed target center is outside the screen; reveal it first")
    return target


def _string(value, name, *, required=False, limit=4000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError("%s must be %sstring of at most %d characters" % (name, "a nonempty " if required else "a ", limit))
    return value


def _items(value, name, limit=30):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("%s must be a list of at most %d items" % (name, limit))
    return value


def _evidence(item, history_size):
    _string(item.get("evidence"), "evidence", required=True)
    refs = _items(item.get("steps"), "steps", 60)
    if any(type(n) is not int or not 1 <= n <= history_size for n in refs):
        raise ValueError("Evidence refers to an action that has not occurred")


def _requirements(value, history_size, previous):
    rows = _items(value, "requirements")
    if not rows:
        raise ValueError("Checklist must cover the original request")
    saved = {r["id"]: r["description"] for r in previous}
    updates = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid requirement")
        identifier = _string(row.get("id"), "requirement id", required=True, limit=80)
        if identifier in updates:
            raise ValueError("Duplicate requirement id")
        if not saved:
            _string(row.get("description"), "requirement description", required=True)
        if row.get("status") not in ("pending", "uncertain", "satisfied"):
            raise ValueError("Invalid requirement status")
        _string(row.get("evidence"), "evidence")
        if row["status"] == "satisfied":
            _evidence(row, history_size)
        else:
            refs = _items(row.get("steps"), "steps", 60)
            if any(type(n) is not int or not 1 <= n <= history_size for n in refs):
                raise ValueError("Invalid history reference")
        updates[identifier] = {"id": identifier, "status": row["status"],
                               "evidence": row["evidence"], "steps": row["steps"],
                               "description": saved.get(identifier, row.get("description"))}
    if saved and updates.keys() != saved.keys():
        raise ValueError("Saved checklist requirements cannot be removed or replaced; update every saved id exactly once")
    # Descriptions belong to the program after creation, even if a model repeats them.
    return [updates[identifier] for identifier in (saved or updates)]


def parse_plan(output, history_size, previous):
    if not isinstance(output, dict) or output.get("status") not in ("continue", "complete", "blocked"):
        raise ValueError("Planner must choose continue, complete or blocked")
    requirements = _requirements(output.get("requirements"), history_size, previous)
    _string(output.get("summary"), "progress summary", required=True, limit=8000)
    _string(output.get("reason"), "planner reason", required=True)
    _string(output.get("answer", ""), "answer", limit=12000)
    plan = _items(output.get("plan"), "plan")
    for step in plan:
        _string(step, "plan step", required=True)
    continuing = output["status"] == "continue"
    _string(output.get("subgoal"), "subgoal", required=continuing)
    _string(output.get("success_condition"), "success condition", required=continuing)
    if continuing and not plan:
        raise ValueError("Continuing requires a remaining plan")
    if not continuing and (plan or output["subgoal"] or output["success_condition"]):
        raise ValueError("Terminal proposal cannot include pending actions")
    if output["status"] == "complete" and any(r["status"] != "satisfied" for r in requirements):
        raise ValueError("Completion proposal has unmet requirements")
    memory, keys, facts = [], set(), set()
    for fact in _items(output.get("memory"), "memory", 20):
        if not isinstance(fact, dict):
            raise ValueError("Invalid memory fact")
        key = _string(fact.get("key"), "memory key", required=True, limit=80).strip().casefold()
        if key in keys:
            raise ValueError("Memory keys must be unique; consolidate each fact once")
        keys.add(key)
        normalized = " ".join(_string(fact.get("fact"), "memory fact", required=True, limit=1000).split()).casefold()
        _evidence(fact, history_size)
        if normalized not in facts:
            memory.append({"key": key, "fact": fact["fact"], "evidence": fact["evidence"], "steps": fact["steps"]})
            facts.add(normalized)
    return {**output, "requirements": requirements, "memory": memory}


def parse_review(output, history_size, requirements, proposed_answer=""):
    if not isinstance(output, dict) or output.get("status") not in ("confirmed", "continue", "blocked"):
        raise ValueError("Review must choose confirmed, continue or blocked")
    rows = _requirements(output.get("requirements"), history_size, requirements)
    _string(output.get("summary"), "review summary", required=True, limit=8000)
    _string(output.get("reason"), "review reason", required=True)
    _string(output.get("answer", ""), "answer", limit=12000)
    if output["status"] == "confirmed" and any(r["status"] != "satisfied" for r in rows):
        raise ValueError("Confirmed review requires positive evidence for every requirement")
    if output["status"] == "confirmed" and proposed_answer.strip() and not output.get("answer", "").strip():
        raise ValueError("Review must return the verified findings instead of silently accepting the proposed answer")
    return {**output, "requirements": rows}


class VisualOutputError(RuntimeError):
    """Executor output was repaired unsuccessfully; no device action was issued."""


def parse_execution(output, screen, installed_apps=(), *, page=None):
    """Validate the action/control contract; task progress belongs to the models."""
    if not isinstance(output, dict) or output.get("status") not in {"act", "replan", "complete"}:
        raise ValueError("Executor must choose act, replan or complete")
    _string(output.get("summary"), "executor progress", required=True, limit=8000)
    _string(output.get("reason"), "executor reason", required=True)
    _string(output.get("answer", ""), "answer", limit=12000)
    parsed = None
    if output["status"] == "act":
        parsed = parse_action(output, screen, installed_apps, page=page)
    else:
        if any(k in output for k in ("action", "index", "point", "box", "start", "end", "text", "package")):
            raise ValueError("Control replies cannot include a device action")
    return {**output, "parsed_action": parsed}


def parse_action(output, screen, installed_apps=(), *, page=None):
    if not isinstance(output, dict):
        raise ValueError("Executor must return one action object")
    operation = output.get("action")
    if not isinstance(operation, str):
        raise ValueError("Executor action must be a string")
    reason = _string(output.get("reason"), "action reason", required=True)
    _string(output.get("expected_effect"), "expected effect", required=True)
    action = {"id": "visual", "label": reason}
    target = None
    if "index" in output:
        if operation not in {"CLICK", "LONG_PRESS", "TYPE_TEXT"} or "point" in output:
            raise ValueError("Index is supported for CLICK, LONG_PRESS and TYPE_TEXT, and cannot accompany point")
        target = indexed_target(page, output["index"], operation)
        action.update({k: target[k] for k in ("center", "bounds", "label", "role")})
        action.update(index=output["index"], a11y_fingerprint=page["a11y_fingerprint"])

    def point(value):
        if (not isinstance(value, list) or len(value) != 2
                or any(type(n) is not int or not 0 <= n <= 1000 for n in value)):
            raise ValueError("Coordinates must be two integers within 0..1000")
        return [round(n * (size - 1) / 1000) for n, size in zip(value, screen)]

    def duration(name, default, lower, upper):
        value = output.get(name, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError("%s must be within %s..%s" % (name, lower, upper))
        return value

    text = None
    if operation in {"CLICK", "LONG_PRESS"}:
        action["kind"] = "click" if operation == "CLICK" else "long_press"
        if target is None:
            action["center"] = point(output.get("point"))
        if operation == "LONG_PRESS":
            action["duration_ms"] = round(duration("duration_ms", 700, 100, 5000))
    elif operation == "CLICK_AREA":
        box = output.get("box")
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("CLICK_AREA requires box [left,top,right,bottom]")
        first, last = point(box[:2]), point(box[2:])
        if first[0] >= last[0] or first[1] >= last[1]:
            raise ValueError("CLICK_AREA box must have positive area")
        action.update(kind="click", center=[round((a + b) / 2) for a, b in zip(first, last)])
    elif operation == "SWIPE":
        action.update(kind="swipe", start=point(output.get("start")), end=point(output.get("end")),
                      duration_ms=round(duration("duration_ms", 300, 100, 5000)))
        if action["start"] == action["end"]:
            raise ValueError("SWIPE must move")
    elif operation == "TYPE_TEXT":
        text = _string(output.get("text"), "field text", required=True, limit=2000)
        current = _string(target.get("value", "") if target else output.get("current_text"), "current field value", limit=2000)
        if target and "current_text" in output and output["current_text"] != current:
            raise ValueError("current_text contradicts the indexed field's observed value")
        if type(output.get("clear")) is not bool:
            raise ValueError("TYPE_TEXT requires boolean clear")
        # One replacement path also handles appending, including Portal's clear-and-insert API.
        if not output["clear"]:
            text = _string(current + text, "combined field value", required=True, limit=2000)
        action.update(kind="fill", value=current)
        if "point" in output:
            action["center"] = point(output["point"])
    elif operation == "OPEN_APP":
        package = output.get("package")
        if not isinstance(package, str) or package not in installed_apps or not re.fullmatch(r"[\w]+(?:\.[\w]+)+", package):
            raise ValueError("OPEN_APP must select an identifier from installed_apps")
        action.update(kind="launch", package=package)
    elif operation in {"BACK", "ENTER"}:
        action.update(kind="key", keycode=4 if operation == "BACK" else 66)
    elif operation in {"HOME", "WAIT"}:
        action["kind"] = operation.lower()
        if operation == "WAIT":
            action["duration"] = duration("duration", 0.5, 0.1, 10)
    else:
        raise ValueError("Unsupported executor action; completion is owned by the planner/reviewer")
    return operation, action, text


class VisualAgent:
    def __init__(self, state, reserve_call, capture, installed_apps):
        self.state, self.reserve_call = state, reserve_call
        self.capture, self.installed_apps = capture, installed_apps
        self.previous_page = None  # Images stay outside persistent JSON state.
        self.fast = state["settings"]["vision_mode"] == "fast"

    def context(self, role):
        state = self.state
        progress = state["progress"]
        def page_view(page):
            if not page:
                return None
            return {**{k: page.get(k) for k in ("app", "activity", "screen")}, "text": (page.get("text") or "")[:1500]}

        def outcome(item, detailed=False):
            result = {k: item.get(k) for k in ("step", "mode", "operation", "text", "success", "error") if item.get(k) is not None}
            result["target"] = (item.get("action") or "")[:200]
            if detailed:
                result.update(expected_effect=item.get("expected_effect"), subgoal=item.get("subgoal"),
                              page_before=page_view(item.get("page_before")), page_after=page_view(item.get("page_after")))
            return result

        progress_keys = {
            "planner": ("summary", "requirements", "plan", "memory", "feedback", "revision"),
            "executor": ("summary", "requirements", "plan", "success_condition", "memory", "feedback"),
            "verifier": ("summary", "requirements", "memory", "proposed_answer"),
        }[role]
        context = {
            "vision_mode": state["settings"]["vision_mode"],
            "goal": state["goal"], "current_page": page_view(state["page"]),
            "recent_outcomes": [outcome(h, True) for h in state["history"][-(2 if role == "executor" else 3):]],
            "progress": {k: progress[k] for k in progress_keys},
            "remaining_actions": max(0, state["action_limit"] - len(state["history"])),
        }
        initial_fast = self.fast and not progress["summary"]
        if role != "executor" or initial_fast:
            context["attempted_actions"] = [outcome(h) for h in state["history"]]
        if role == "executor":
            # Preserve early supplied/read values without resending the whole navigation history.
            context["entered_values"] = [outcome(h) for h in state["history"] if h.get("text")]
            context["elements"] = visual_elements(state["page"])
        if role == "planner" or initial_fast:
            context["recovery_reason"] = state["recovery_reason"]
            handoff = state["handoff"]
            context["handoff"] = ({"reason": handoff["reason"], "after_step": handoff["after_step"],
                                   "last_fast_observation": page_view(handoff.get("last_fast_observation"))} if handoff else None)
        hint = progress["subgoal"]
        if not progress["plan"]:
            hint = state["goal"]
        if role != "verifier" and ("launcher" in (state["page"].get("app") or "").lower()
                                   or any(word in hint.lower() for word in ("open", "launch", "start", "打开", "启动"))):
            context["installed_apps"] = self.installed_apps()
        return context

    def request(self, role, prompt, validate):
        model = os.environ.get("VISION_%s_MODEL" % role.upper()) or os.environ["VISION_MODEL"]
        url = os.environ.get("VISION_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
        effort = os.environ.get("VISION_%s_REASONING" % role.upper()) or ("none" if role == "executor" else "low")
        reasoning_options(url, model, effort)  # Validate configuration before spending a request.
        output_tokens = OUTPUT_TOKENS
        context = self.context(role)
        context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        content = [{"type": "text", "text": context_text}]
        for label, page in (("previous", self.previous_page), ("current", self.state["page"])):
            if page and page.get("screenshot"):
                content += [{"type": "text", "text": "%s image; native screen %s, model image %s; coordinates 0..1000" % (
                    label, page["screen"], page.get("model_screen", page["screen"]))},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + page["screenshot"]}}]
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
        timeout = float(os.environ.get("VISION_MODEL_TIMEOUT", "90"))
        if not 1 <= timeout <= 300:
            raise ValueError("VISION_MODEL_TIMEOUT must be 1..300 seconds")
        for attempt in range(MAX_REPAIRS):
            call = self.reserve_call(role, model)
            controls = reasoning_options(url, model, effort)
            options = {"max_tokens": output_tokens, **controls}
            call.update(requested_reasoning=effort, request_options=options, reasoning_control=bool(controls))
            call["observed"] = {**page_summary(self.state["page"]), "elements": visual_elements(self.state["page"])}
            call["context_chars"] = len(context_text)
            call["image_count"] = sum(item["type"] == "image_url" for item in content)
            started = time.perf_counter()
            raw = None
            error_kind, fatal = "request_error", False
            try:
                body = {"model": model, **options, "response_format": {"type": "json_object"}, "messages": messages}
                call["request_bytes"] = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                call["endpoint_host"] = urlsplit(url).hostname
                response = post_json(
                    url,
                    os.environ["VISION_MODEL_API_KEY"],
                    body,
                    timeout=timeout,
                )
                error_kind = "response_format"
                call["usage"] = response.get("usage") or {}
                details = call["usage"].get("completion_tokens_details") or call["usage"].get("output_tokens_details") or {}
                choice = response["choices"][0]
                message = choice["message"]
                raw = message.get("content")
                call.update(finish_reason=choice.get("finish_reason"), response_model=response.get("model"),
                            reasoning_tokens=details.get("reasoning_tokens"),
                            content_chars=len(raw) if isinstance(raw, str) else 0)
                if call["finish_reason"] == "content_filter" or message.get("refusal"):
                    error_kind, fatal = "response_rejected", True
                    raise ValueError("Provider rejected the response; no device action executed")
                if call["finish_reason"] == "length":
                    error_kind = "output_limit"
                    raise ValueError("Output token limit reached; incomplete response will not be executed")
                if raw is None or isinstance(raw, str) and not raw.strip():
                    error_kind = "empty_content"
                    raise ValueError("Model returned an empty JSON body; no device action executed")
                parsed = json.loads(raw)
                call["output"] = parsed  # Keep rejected JSON too, without recording hidden reasoning.
                error_kind = "invalid_output"
                result = validate(parsed)
                call.update(status="ok", output=parsed)
                return result, call
            except (RuntimeError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                failure = str(error)
                retry_action = "retry_request" if error_kind == "request_error" else "repair_json"
                if error_kind in {"output_limit", "empty_content"}:
                    if call.get("reasoning_tokens"):
                        if controls and effort != "none":
                            effort, retry_action = "none", "disable_reasoning"
                        else:
                            fatal = True
                            failure += "; reasoning continued while disabled or no compatible control is available"
                    elif error_kind == "output_limit":
                        if output_tokens < MAX_OUTPUT_TOKENS:
                            output_tokens, retry_action = MAX_OUTPUT_TOKENS, "increase_output_limit"
                        else:
                            fatal = True
                            failure += "; maximum output budget already reached"
                if fatal or attempt + 1 == MAX_REPAIRS:
                    retry_action = "stop"
                call.update(status="error", error=failure[:500], error_kind=error_kind, retry_action=retry_action)
                self.state["events"].append({"type": "visual_retry", "role": role, "attempt": attempt + 1,
                                             "error": call["error"], "error_kind": error_kind, "retry_action": retry_action,
                                             "finish_reason": call.get("finish_reason"), "reasoning_tokens": call.get("reasoning_tokens"),
                                             "max_tokens": options["max_tokens"],
                                             "elapsed_ms": call["elapsed_ms"] + round((time.perf_counter() - started) * 1000)})
                if retry_action == "stop":
                    error_type = (VisualOutputError if role == "executor" and error_kind in {"invalid_output", "response_format"}
                                  else RuntimeError)
                    raise error_type("Visual %s failed after %d attempts (%s; finish_reason=%s; reasoning_tokens=%s; max_tokens=%d): %s" % (
                        role, attempt + 1, error_kind, call.get("finish_reason"), call.get("reasoning_tokens"),
                        options["max_tokens"], call["error"])) from None
                if isinstance(raw, str) and raw.strip() and error_kind in {"response_format", "invalid_output"}:
                    messages.append({"role": "assistant", "content": raw[:16000]})
                messages.append({"role": "user", "content": "No action was executed. Correct this response error: " + call["error"] +
                                 ". Return one complete, concise JSON object. Retry adjustment: " + retry_action})
            finally:
                call["latency_ms"] = round((time.perf_counter() - started) * 1000)

    def decide(self):
        state, progress = self.state, self.state["progress"]
        if self.fast:
            # No planning call: the reviewer still checks the entire original request.
            if not progress["requirements"]:
                progress["requirements"] = [{"id": "r1", "description": state["goal"],
                                              "status": "pending", "evidence": "", "steps": []}]
            progress["needs_plan"] = False
        if not self.fast and progress["needs_plan"]:
            proposal, call = self.request("planner", PLANNER,
                                          lambda o: parse_plan(o, len(state["history"]), progress["requirements"]))
            progress.update({k: proposal[k] for k in ("summary", "requirements", "plan", "subgoal", "success_condition")})
            progress.update(memory=proposal["memory"], revision=progress["revision"] + 1,
                            needs_plan=False, proposed_answer="")
            state["events"].append({"type": "visual_plan", "subgoal": progress["subgoal"],
                                    "summary": progress["summary"], "latency_ms": call["latency_ms"],
                                    "elapsed_ms": call["elapsed_ms"] + call["latency_ms"]})
            if proposal["status"] == "blocked":
                return self.decision("BLOCKED", None, None, call, proposal["reason"])
            if proposal["status"] == "complete":
                return self.review(proposal.get("answer", ""))
            # Only a new plan needs an extra capture after slow planning. On normal
            # turns the parent already supplied the post-action/current observation.
            planned_page = state["page"]
            state["page"] = self.capture()
            if any(planned_page.get(k) != state["page"].get(k) for k in ("app", "activity", "screen")):
                progress["needs_plan"] = True
                state["events"].append({"type": "reobserve", "reason": "visual_plan_stale", "elapsed_ms": state["elapsed_ms"]})
                return self.decision("REPLAN", None, None, call, "Window or screen dimensions changed during planning")
        try:
            output, call = self.request("executor", EXECUTOR,
                                        lambda o: parse_execution(o, state["page"]["screen"],
                                                                  self.installed_apps() if isinstance(o, dict) and o.get("action") == "OPEN_APP" else (),
                                                                  page=state["page"]))
        except VisualOutputError as error:
            operation = "REPLAN" if self.feedback("invalid_executor_output", str(error)) else "BLOCKED"
            return self.decision(operation, None, None, state["model_calls"][-1], str(error))
        progress.update(summary=output["summary"], subgoal=output["reason"])
        state["events"].append({"type": "visual_progress", "summary": progress["summary"],
                                "elapsed_ms": call["elapsed_ms"] + call["latency_ms"]})
        if output["status"] == "replan":
            operation = "REPLAN" if self.feedback("plan_deviation", output["reason"]) else "BLOCKED"
            return self.decision(operation, None, None, call, output["reason"])
        if output["status"] == "complete":
            return self.review(output.get("answer", ""))
        progress["success_condition"] = output["expected_effect"]
        operation, action, text = output["parsed_action"]
        return self.decision(operation, action, text, call, output["reason"], expected_effect=output["expected_effect"])

    def review(self, proposed_answer):
        state, progress = self.state, self.state["progress"]
        progress["proposed_answer"] = proposed_answer
        # Keep the real pre-action image, rather than replacing it with a second
        # post-action image immediately before review.
        state["page"] = self.capture()
        review, call = self.request("verifier", VERIFIER,
                                   lambda o: parse_review(o, len(state["history"]), progress["requirements"], proposed_answer))
        progress.update(summary=review["summary"], requirements=review["requirements"], success_condition="")
        state["events"].append({"type": "visual_review", "status": review["status"],
                                "reason": review["reason"], "latency_ms": call["latency_ms"],
                                "elapsed_ms": call["elapsed_ms"] + call["latency_ms"]})
        if review["status"] == "confirmed":
            state["answer"] = review.get("answer") or proposed_answer
            evidence = "\n".join(r["description"] + ": " + r["evidence"] for r in review["requirements"])
            return self.decision("DONE", None, None, call, review["reason"], evidence=evidence)
        if review["status"] == "blocked":
            return self.decision("BLOCKED", None, None, call, review["reason"])
        if not self.feedback("completion_rejected", review["reason"]):
            return self.decision("BLOCKED", None, None, call, "Completion review repeatedly failed: " + review["reason"])
        return self.decision("REPLAN", None, None, call, review["reason"])

    def decision(self, operation, action, text, call, reason, **extra):
        return {"operation": operation, "choice": "visual" if action else operation,
                "action": action, "text": text, "target": action.get("index") if action else None, "confidence": None,
                "probabilities": {}, "operation_probabilities": {}, "target_probabilities": {},
                "target_confidence": None, "goal": {"satisfied": None, "probability": None},
                "model": call["model"], "usage": call.get("usage", {}), "latency_ms": call["latency_ms"],
                "role": call["role"],
                "observed": call["observed"], "mode": "vision", "reason": reason,
                "subgoal": self.state["progress"]["subgoal"], **extra}

    def feedback(self, kind, message):
        progress = self.state["progress"]
        progress["needs_plan"] = not self.fast
        progress["feedback"] = {"kind": kind, "message": message, "step": len(self.state["history"])}
        progress["recoveries"] += 1
        self.state["events"].append({"type": "visual_replan", **progress["feedback"],
                                    "vision_mode": self.state["settings"]["vision_mode"],
                                    "elapsed_ms": round((time.perf_counter() - self.state["started_at"]) * 1000)
                                    if self.state["started_at"] is not None else self.state["elapsed_ms"]})
        if progress["recoveries"] >= MAX_RECOVERIES:
            self.state.update(status="blocked", answer="Visual recovery exhausted: " + message)
            return False
        return True
