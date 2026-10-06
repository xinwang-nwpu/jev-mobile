"""One bounded phone loop: fast A11Y decisions, visual recovery after failure.

Both paths share observation, execution, history and budgets. Visual coordinates
are validated before reaching the executor; model output never becomes shell code.
"""

import base64
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import ParseError

from .device import Device, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS
from . import vision, visual_images

# Two vetoes allow fresh evidence before visual review, or legacy DONE acceptance.
MAX_DONE_VETOES = 2


def _observed(decision: Dict[str, Any]) -> Dict[str, Any]:
    """The state the model actually saw, kept in the trace for debugging."""
    if decision.get("mode") == "vision":
        return decision["observed"]
    state = decision.get("request", {}).get("state", {})
    page = dict(state.get("page", {}))
    page["text"] = str(page.get("text", ""))[:800]
    return {"page": page, "elements": state.get("elements", [])}


def usage_totals(state: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate token usage over a run, tolerating both key styles seen from providers:
    TypeSafe (input/output_tokens) and OpenAI-compatible text helpers (prompt/completion)."""
    decisions = state.get("model_calls", state.get("decisions", []))
    text_calls = state.get("text_calls", [])
    decision_in = sum((d.get("usage") or {}).get("input_tokens", (d.get("usage") or {}).get("prompt_tokens", 0)) or 0 for d in decisions)
    decision_out = sum((d.get("usage") or {}).get("output_tokens", (d.get("usage") or {}).get("completion_tokens", 0)) or 0 for d in decisions)
    text_in = text_out = 0
    for call in text_calls:
        usage = call.get("usage") or {}
        text_in += usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        text_out += usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return {
        "decision": {"requests": len(decisions), "input_tokens": decision_in, "output_tokens": decision_out},
        "text": {"requests": len(text_calls), "input_tokens": text_in, "output_tokens": text_out},
        "total": {"input_tokens": decision_in + text_in, "output_tokens": decision_out + text_out},
    }


class Agent:
    def __init__(
        self,
        task: str,
        *,
        device: Optional[Device] = None,
        adb_path: Optional[str] = None,
        serial: Optional[str] = None,
        start_package: Optional[str] = None,
        record_dir: Optional[str] = None,
        screenshots: Optional[bool] = None,
        action_interval: float = 0.0,
        vision_fallback: bool = True,
        vision_only: bool = False,
    ):
        task = task.strip() if isinstance(task, str) else ""
        if not task:
            raise ValueError("Supply a task")
        if action_interval < 0:
            raise ValueError("action_interval must be >= 0")
        if vision_only and not vision.configured():
            raise ValueError("Vision-only mode needs VISION_MODEL_API_KEY and VISION_MODEL")
        self.action_interval = float(action_interval)
        self.device = device or Device(adb_path, serial, prepare_portal=not vision_only)
        self.record_dir = Path(record_dir) if record_dir else None
        self.record = bool(self.record_dir)
        self._image_number = 0
        # None = recording implies screenshots; an explicit value always wins.
        self.screenshots = bool(self.record_dir) if screenshots is None else bool(screenshots)
        self.vision_fallback = vision_fallback and vision.configured()
        self.state: Dict[str, Any] = dict(
            goal=task,
            page=None,
            decision=None,
            history=[],
            status="ready",
            decisions=[],
            text_calls=[],
            events=[],
            elapsed_ms=0,
            started_at=None,
            done_vetoes=0,
            mode="vision" if vision_only else "fast",
            recovery_reason="vision_only" if vision_only else None,
            handoff=None,
            progress=vision.initial_progress(),
            model_calls=[],
            action_limit=MAX_STEPS,
            answer="",
        )
        self._apps = None
        self.visual = vision.VisualAgent(self.state, self._reserve_call, self._capture_visual, self._installed_apps)
        try:
            if start_package:
                self.device.launch(start_package)
            self.state["page"] = self._observe()
        except Exception:
            self.device.close()
            raise
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            self._record_page()

    # -- public view ------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        return {
            **{k: v for k, v in self.state.items() if k != "page"},
            "page": {k: v for k, v in self.state["page"].items() if k not in {"screenshot", "visual_signature"}},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def trace(self) -> Dict[str, Any]:
        state = self.state
        return {
            "goal": state["goal"],
            "status": state["status"],
            "elapsed_ms": state["elapsed_ms"],
            "mode": state["mode"],
            "handoff": state["handoff"],
            "progress": state["progress"],
            "model_calls": state["model_calls"],
            "answer": state["answer"],
            "usage": usage_totals(state),
            "history": state["history"],
            "text_calls": state["text_calls"],
            "final_page": {k: state["page"].get(k) for k in ("app", "activity", "source", "screen")},
            "events": state["events"],
            "decisions": [
                {
                    **{k: d.get(k) for k in ("operation", "target", "confidence", "target_confidence", "latency_ms", "mode", "model", "action", "evidence", "reason")},
                    "goal": d.get("goal"),
                    "observed": _observed(d),
                }
                for d in state["decisions"]
            ],
        }

    # -- loop -------------------------------------------------------------

    def tick(self) -> Dict[str, Any]:
        state = self.state
        try:
            if not self._predict():
                return self.snapshot()
            return self._act()
        except StalePage:
            state["decision"] = None
            state["status"] = "ready"
            if state["mode"] == "vision":
                state["progress"]["needs_plan"] = True
            return self._recover_page()
        except (RuntimeError, ValueError):
            if state["status"] not in {"done", "blocked"} and self._handoff("execution_error"):
                return self._recover_page()
            state["status"] = "blocked"
            raise

    def _handoff(self, reason: str) -> bool:
        # ponytail: one-way handoff; add return-to-fast only when visual cost warrants it.
        state = self.state
        if not self.vision_fallback or state["mode"] == "vision" or len(state["history"]) >= MAX_STEPS:
            return False
        state.update(mode="vision", recovery_reason=reason, decision=None, status="ready", done_vetoes=0)
        state["handoff"] = {"reason": reason, "after_step": len(state["history"]),
                             "last_fast_observation": vision.page_summary(state["page"])}
        self.visual.previous_page = state["page"]
        state["events"].append({"type": "handoff", "reason": reason, "elapsed_ms": self._elapsed()})
        return True

    def _observe(self) -> Dict[str, Any]:
        if self.state["mode"] == "vision":
            return self.device.observe_visual()
        try:
            page = self.device.observe(screenshot=self.screenshots)
        except (RuntimeError, ValueError, ParseError):
            if not self._handoff("observation_error"):
                raise
            return self.device.observe_visual()
        if not any(a["kind"] in {"click", "fill"} for a in page["actions"]) and self._handoff("no_targets"):
            return self.device.observe_visual()
        return page

    def _recover_page(self) -> Dict[str, Any]:
        try:
            self.state["page"] = self._observe()
        except (RuntimeError, ValueError):
            self.state["status"] = "blocked"
            raise
        self.state["elapsed_ms"] = self._elapsed()
        self._record_page()
        return self.snapshot()

    def _capture_visual(self):
        page = self.device.observe_visual()
        self.state["elapsed_ms"] = self._elapsed()
        self._record_page(page)
        return page

    def _installed_apps(self):
        if self._apps is None:
            try:
                self._apps = self.device.list_apps() if hasattr(self.device, "list_apps") else []
            except (RuntimeError, ValueError):
                self._apps = []
        return self._apps

    def _reserve_call(self, role, model):
        state = self.state
        # Budget actual inference attempts, including schema repairs and review passes.
        if len(state["model_calls"]) >= MAX_STEPS * 4:
            state["status"] = "blocked"
            raise ValueError("Reached the model-call budget")
        call = {"role": role, "model": model, "status": "pending", "usage": {}, "elapsed_ms": self._elapsed()}
        state["model_calls"].append(call)
        return call

    def _record_page(self, page=None) -> None:
        page = self.state["page"] if page is None else page
        if self.record and "screenshot" in page:
            self._image_number += 1
            name = "%06d-%03d.png" % (self.state["elapsed_ms"], self._image_number)
            (self.record_dir / name).write_bytes(base64.b64decode(page["screenshot"]))
            page["image_file"] = name

    def _predict(self) -> bool:
        state = self.state
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        if self.device.activity_changed(state["page"]):
            state["events"].append({"type": "reobserve", "reason": "focus_changed", "elapsed_ms": self._elapsed()})
            if state["mode"] == "vision":
                state["progress"]["needs_plan"] = True
            state["page"] = self._observe()
            state["elapsed_ms"] = self._elapsed()
            self._record_page()
        state["decision"] = None
        if state["status"] in {"done", "blocked"}:
            raise ValueError("This run has stopped; start a fresh Agent.")
        try:
            if state["mode"] == "vision":
                state["decision"] = self.visual.decide()
            else:
                call = self._reserve_call("fast", "jev")
                try:
                    state["decision"] = choose(state["page"], state["goal"], state["history"])
                    call.update(status="ok", model=state["decision"]["model"], usage=state["decision"]["usage"],
                                latency_ms=state["decision"]["latency_ms"])
                except Exception as error:
                    call.update(status="error", error=str(error)[:500])
                    raise
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
            if state["status"] == "blocked" or not self._handoff("model_error"):
                state["status"] = "blocked"
                raise RuntimeError(str(error)) from None
            self._recover_page()
            return False
        state["decision"]["mode"] = state["mode"]
        if state["decision"]["operation"] != "DONE":
            state["done_vetoes"] = 0
        state["decisions"].append(
            {**state["decision"], "fingerprint": state["page"]["fingerprint"], "elapsed_ms": self._elapsed()}
        )
        state["events"].append(
            {
                "type": "visual_decision" if state["mode"] == "vision" else "decision",
                "elapsed_ms": self._elapsed(),
                "operation": state["decision"]["operation"],
                "operation_probabilities": state["decision"]["operation_probabilities"],
                "confidence": state["decision"]["confidence"],
                "latency_ms": state["decision"]["latency_ms"],
                "target": state["decision"]["target"],
                "target_probabilities": state["decision"]["target_probabilities"],
                "target_confidence": state["decision"]["target_confidence"],
                "goal_probability": state["decision"]["goal"]["probability"],
                "reason": state["decision"].get("reason"),
                "model": state["decision"]["model"],
            }
        )
        state["status"] = "predicted"
        return True

    def _accept_stop(self, status: str) -> Dict[str, Any]:
        """Accept DONE/BLOCKED only on a fresh screen; a changed page forces a re-decision."""
        state = self.state
        # ponytail: visual stops check focus; pixel equality rejects animated screens.
        fresh = (not self.device.activity_changed(state["page"]) if state["mode"] == "vision"
                 else self.device.fresh(state["page"]))
        if not fresh:
            state["status"] = "ready"
            state["events"].append({"type": "stale_done", "elapsed_ms": self._elapsed()})
            raise StalePage("Screen changed since the decision. Choose again.")
        state["status"] = status
        state["elapsed_ms"] = self._elapsed()
        return self.snapshot()

    def _veto_done(self, goal: Dict[str, Any]) -> Dict[str, Any]:
        """The goal gate rejected the DONE: run the built-in WAIT instead, then re-decide
        with fresh evidence. Waiting lets a dynamic screen (e.g. playing media) evolve."""
        state = self.state
        state["done_vetoes"] += 1
        state["events"].append(
            {"type": "done_vetoed", "goal_probability": goal.get("probability"), "elapsed_ms": self._elapsed()}
        )
        wait = next(a for a in state["page"]["actions"] if a["id"] == "wait")
        state["decision"].update(choice=wait["id"], action=wait)
        return self._act()

    def _act(self) -> Dict[str, Any]:
        state = self.state
        decision, page = state["decision"], state["page"]
        if not decision:
            raise ValueError("Predict before acting")
        selected = decision["choice"]
        if selected == "REPLAN":
            if state["status"] != "blocked":
                state["status"] = "ready"
            return self.snapshot()
        goal = decision.get("goal") or {}
        if selected in {"DONE", "BLOCKED"}:
            if state["mode"] == "vision" and selected == "BLOCKED":
                state["answer"] = decision.get("reason", "")
            if selected == "BLOCKED" and self._handoff("blocked"):
                return self._recover_page()
            # Repeated disagreement goes to vision when available; otherwise keep
            # the legacy third-DONE rule.
            if selected == "DONE" and goal.get("satisfied") is False and state["done_vetoes"] < MAX_DONE_VETOES:
                return self._veto_done(goal)
            if selected == "DONE" and goal.get("satisfied") is False and self._handoff("completion_conflict"):
                return self._recover_page()
            status = "done" if selected == "DONE" else "blocked"
            return self._accept_stop(status)
        if goal.get("satisfied") is True:
            # ...and the gate can end the run even when the operation head wanted another action.
            state["events"].append(
                {"type": "goal_done", "goal_probability": goal.get("probability"), "elapsed_ms": self._elapsed()}
            )
            return self._accept_stop("done")
        action = decision.get("action") or next(a for a in page["actions"] if a["id"] == selected)
        if len(state["history"]) >= MAX_STEPS:
            state["status"] = "blocked"
            raise ValueError("Stopped at the %d-action budget" % MAX_STEPS)
        text, helper = decision.get("text"), None
        if action["kind"] == "fill" and state["mode"] == "fast":
            context = field_context(state["goal"], action, page, state["history"])
            text, helper = field_text(context)
            state["text_calls"].append({**helper, "field": action["label"], "value": text})
        if state["mode"] == "vision" and self.device.activity_changed(page):
            raise StalePage("Window changed before visual execution")
        if state["mode"] == "vision" and "index" in action and not self.device.fresh_index(page):
            state["events"].append({"type": "reobserve", "reason": "a11y_index_stale", "elapsed_ms": self._elapsed()})
            raise StalePage("A11Y changed since the indexed decision; choose from a new snapshot")
        execution_error = None
        try:
            self.device.act(action, text=text)
        except (RuntimeError, ValueError) as error:
            execution_error = str(error)[:500]
        if self.action_interval:
            # Extra pacing between actions; also lets animations settle before observing.
            time.sleep(self.action_interval)
        state["elapsed_ms"] = self._elapsed()
        # Record execution before observing. A failed post-action observation must not erase it.
        state["history"].append(
            {
                "step": len(state["history"]) + 1,
                "action": action["label"],
                "kind": action["kind"],
                "choice": selected,
                "mode": state["mode"],
                "device_action": dict(action),
                "success": execution_error is None,
                "error": execution_error,
                "expected_effect": decision.get("expected_effect"),
                "subgoal": decision.get("subgoal"),
                "page_before": vision.page_summary(page),
                # Snapshot ids can shift when a menu appears; identify the actual control.
                "action_signature": [action.get(k) for k in (
                    ("kind", "center", "start", "end", "keycode", "package") if state["mode"] == "vision"
                    else ("kind", "label", "role", "bounds", "center", "start", "end", "keycode"))],
                "fingerprint_before": page["fingerprint"],
                "probability": decision["probabilities"].get(selected),
                "confidence": decision["confidence"],
                "latency_ms": decision["latency_ms"],
                "text": text,
                "text_helper": helper["model"] if helper else None,
                "text_latency_ms": helper["latency_ms"] if helper else 0,
                "operation": decision["operation"],
                "target": decision["target"],
                "page_changed": None,
                "activity_before": page["activity"],
                "usage": decision["usage"],
                "executed_ms": self._elapsed(),
                "elapsed_ms": state["elapsed_ms"],
            }
        )
        if state["mode"] == "vision":
            self.visual.previous_page = page
        state["page"] = self._observe()
        state["elapsed_ms"] = self._elapsed()
        self._record_page()
        state["history"][-1].update(
            page_changed=(visual_images.changed(page, state["page"]) if page.get("source") == state["page"].get("source") == "vision"
                          else state["page"]["fingerprint"] != page["fingerprint"]),
            fingerprint_after=state["page"]["fingerprint"],
            page_after=vision.page_summary(state["page"]),
            activity=state["page"]["activity"],
            elapsed_ms=state["elapsed_ms"],
        )
        if execution_error:
            if state["mode"] == "fast" and self._handoff("execution_error"):
                return self._recover_page()
            if state["mode"] == "vision":
                state["status"] = "ready"
                self.visual.feedback("execution_error", execution_error)
                return self.snapshot()
            state["status"] = "blocked"
            raise RuntimeError(execution_error)
        if state["mode"] == "vision" and state["history"][-1]["page_changed"]:
            state["progress"]["recoveries"] = 0
            state["progress"]["feedback"] = {}
        repeated = state["history"][-3:]
        if len(repeated) == 3 and all(h["mode"] == state["mode"] and h["page_changed"] is False
                                       and h["kind"] != "wait" for h in repeated):
            state["status"] = "blocked"
            state["events"].append({"type": "stuck", "elapsed_ms": self._elapsed()})
        else:
            state["status"] = "ready"
            toggles = state["history"][-4:]
            if (len(toggles) == 4 and all(h["mode"] == state["mode"] and h["kind"] == "click" for h in toggles)
                    and all(h.get("action_signature") == toggles[0].get("action_signature") for h in toggles)
                    and all(h.get("fingerprint_before") and h.get("fingerprint_after") for h in toggles)
                    and all(toggles[i]["fingerprint_before"] == toggles[i + 2]["fingerprint_before"]
                            and toggles[i]["fingerprint_after"] == toggles[i + 2]["fingerprint_after"]
                            for i in range(2))):
                state["status"] = "blocked"
                state["events"].append({"type": "cycle", "elapsed_ms": self._elapsed(), "action": action["label"]})
        if state["status"] == "blocked" and self._handoff(state["events"][-1]["type"]):
            return self._recover_page()
        if state["status"] == "blocked" and state["mode"] == "vision":
            state["status"] = "ready"
            self.visual.feedback(state["events"][-1]["type"], "Recent actions did not progress; revise the approach")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.tick()

    def _elapsed(self) -> int:
        return round((time.perf_counter() - self.state["started_at"]) * 1000) if self.state["started_at"] else 0

    def close(self) -> None:
        self.device.close()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
