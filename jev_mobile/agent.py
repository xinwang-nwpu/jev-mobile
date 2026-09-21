"""The complete phone-agent loop. Typed choices, observable A11Y state, bounded execution.

One decision per step: the Jev model picks an operation and a target from the last
observation, a small LLM writes text only when the operation is TYPE_TEXT, and the
executor resolves the observed node to coordinates. Model output never becomes
selectors, coordinates, or shell commands.
"""

import base64
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .device import Device, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS


def _observed(decision: Dict[str, Any]) -> Dict[str, Any]:
    """The state the model actually saw, kept in the trace for debugging."""
    state = decision.get("request", {}).get("state", {})
    page = dict(state.get("page", {}))
    page["text"] = str(page.get("text", ""))[:800]
    return {"page": page, "elements": state.get("elements", [])}


def usage_totals(state: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate token usage over a run, tolerating both key styles seen from providers:
    TypeSafe (input/output_tokens) and OpenAI-compatible text helpers (prompt/completion)."""
    decisions = state.get("decisions", [])
    text_calls = state.get("text_calls", [])
    decision_in = sum((d.get("usage") or {}).get("input_tokens", 0) or 0 for d in decisions)
    decision_out = sum((d.get("usage") or {}).get("output_tokens", 0) or 0 for d in decisions)
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
    ):
        task = task.strip() if isinstance(task, str) else ""
        if not task:
            raise ValueError("Supply a task")
        if action_interval < 0:
            raise ValueError("action_interval must be >= 0")
        self.action_interval = float(action_interval)
        self.device = device or Device(adb_path, serial)
        self.record_dir = Path(record_dir) if record_dir else None
        self.record = bool(self.record_dir)
        # None = recording implies screenshots; an explicit value always wins.
        self.screenshots = bool(self.record_dir) if screenshots is None else bool(screenshots)
        try:
            if start_package:
                self.device.launch(start_package)
            page = self.device.observe(screenshot=self.screenshots)
        except Exception:
            self.device.close()
            raise
        self.state: Dict[str, Any] = dict(
            goal=task,
            page=page,
            decision=None,
            history=[],
            status="ready",
            decisions=[],
            text_calls=[],
            events=[],
            elapsed_ms=0,
            started_at=None,
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            if "screenshot" in page:
                (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    # -- public view ------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        return {
            **{k: v for k, v in self.state.items() if k != "page"},
            "page": {k: v for k, v in self.state["page"].items() if k != "screenshot"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def trace(self) -> Dict[str, Any]:
        state = self.state
        return {
            "goal": state["goal"],
            "status": state["status"],
            "elapsed_ms": state["elapsed_ms"],
            "usage": usage_totals(state),
            "history": state["history"],
            "text_calls": state["text_calls"],
            "final_page": {k: state["page"].get(k) for k in ("app", "activity", "source", "screen")},
            "events": state["events"],
            "decisions": [
                {
                    **{k: d[k] for k in ("operation", "target", "confidence", "target_confidence", "latency_ms")},
                    "observed": _observed(d),
                }
                for d in state["decisions"]
            ],
        }

    # -- loop -------------------------------------------------------------

    def tick(self) -> Dict[str, Any]:
        state = self.state
        try:
            self._predict()
            return self._act()
        except StalePage:
            state["decision"] = None
            state["status"] = "ready"
            state["page"] = self.device.observe(screenshot=self.screenshots)
            state["elapsed_ms"] = self._elapsed()
            return self.snapshot()

    def _predict(self) -> None:
        state = self.state
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        if self.device.activity_changed(state["page"]):
            state["events"].append({"type": "reobserve", "reason": "focus_changed", "elapsed_ms": self._elapsed()})
            state["page"] = self.device.observe(screenshot=self.screenshots)
        state["decision"] = None
        if state["status"] in {"done", "blocked"}:
            raise ValueError("This run has stopped; start a fresh Agent.")
        if len(state["decisions"]) >= MAX_STEPS * 2:
            raise ValueError("Reached the model-call budget")
        state["decision"] = choose(state["page"], state["goal"], state["history"])
        state["decisions"].append(
            {**state["decision"], "fingerprint": state["page"]["fingerprint"], "elapsed_ms": self._elapsed()}
        )
        state["events"].append(
            {
                "type": "decision",
                "elapsed_ms": self._elapsed(),
                "operation": state["decision"]["operation"],
                "operation_probabilities": state["decision"]["operation_probabilities"],
                "confidence": state["decision"]["confidence"],
                "latency_ms": state["decision"]["latency_ms"],
                "target": state["decision"]["target"],
                "target_probabilities": state["decision"]["target_probabilities"],
                "target_confidence": state["decision"]["target_confidence"],
            }
        )
        state["status"] = "predicted"

    def _act(self) -> Dict[str, Any]:
        state = self.state
        decision, page = state["decision"], state["page"]
        if not decision:
            raise ValueError("Predict before acting")
        selected = decision["choice"]
        if selected in {"DONE", "BLOCKED"}:
            if not self.device.fresh(page):
                state["status"] = "ready"
                state["events"].append({"type": "stale_done", "elapsed_ms": self._elapsed()})
                raise StalePage("Screen changed since the decision. Choose again.")
            state["status"] = "done" if selected == "DONE" else "blocked"
            state["elapsed_ms"] = self._elapsed()
            return self.snapshot()
        action = next(a for a in page["actions"] if a["id"] == selected)
        if len(state["history"]) >= MAX_STEPS:
            state["status"] = "blocked"
            raise ValueError("Stopped at the %d-action budget" % MAX_STEPS)
        text, helper = None, None
        if action["kind"] == "fill":
            context = field_context(state["goal"], action, page, state["history"])
            text, helper = field_text(context)
            state["text_calls"].append({**helper, "field": action["label"], "value": text})
        self.device.act(action, text=text)
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
                "probability": decision["probabilities"][selected],
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
        state["page"] = self.device.observe(screenshot=self.screenshots)
        state["elapsed_ms"] = self._elapsed()
        state["history"][-1].update(
            page_changed=state["page"]["fingerprint"] != page["fingerprint"],
            activity=state["page"]["activity"],
            elapsed_ms=state["elapsed_ms"],
        )
        if self.record and "screenshot" in state["page"]:
            (self.record_dir / ("%06d.jpg" % state["elapsed_ms"])).write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )
        repeated = state["history"][-3:]
        if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated):
            state["status"] = "blocked"
            state["events"].append({"type": "stuck", "elapsed_ms": self._elapsed()})
        else:
            state["status"] = "ready"
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.tick()

    def _elapsed(self) -> int:
        return round((time.perf_counter() - self.state["started_at"]) * 1000)

    def close(self) -> None:
        self.device.close()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
