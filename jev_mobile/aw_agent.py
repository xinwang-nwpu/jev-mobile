"""AndroidWorld bridge agent: Jev decisions over AndroidWorld's A11Y state.

Observation and execution both go through AndroidWorld so reward computation,
step counting, and screenshots stay under benchmark control. jev-mobile only
supplies the decision policy (model.choose) and the text helper
(model.field_text). This module performs no adb calls of its own.

Mapping:
  AndroidWorld ui_elements -> jev-mobile canonical tree -> snapshot_state page
  choose() decision        -> JSONAction (index-addressed where possible)
  DONE/BLOCKED + goal gate -> AgentInteractionResult(done=True)

Every step stores the full Jev telemetry (probabilities, confidence, goal
gate, latency, token usage) in step_data["jev"] so runs double as signal
collection for routing/escalation experiments.
"""

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from android_world.agents import base_agent
from android_world.env import android_world_controller
from android_world.env import json_action

from .a11y import _flatten, _visible_nodes, normalize_tree, snapshot_state
from .model import choose, field_context, field_text
from .questions import MAX_STEPS

# Same termination policy as jev_mobile.agent.Agent.
MAX_DONE_VETOES = 2
# Consecutive model failures tolerated before the task is abandoned.
MAX_MODEL_ERRORS = 3
# Settle time after executing an action, so app-launch animations finish
# before the next observation (mirrors jev-mobile's action_interval).
SETTLE_SECONDS = float(os.environ.get("JEV_AW_SETTLE_S", "1.5"))
# Retries when the a11y forwarder returns an empty tree (transient flake).
OBSERVE_RETRIES = 3
# Windows that may coexist with the foreground app's window; elements from
# any other package are stale leftovers of apps that already exited (the
# persistent window cache keeps dead trees alive briefly).
SYSTEM_PACKAGES = {
    "",  # nodes without package info
    "com.android.systemui",
    "com.example.androidworld",
    "com.google.android.inputmethod.latin",
    "com.google.android.apps.inputmethod",
}


def _load_env_file(path: Path) -> None:
  """Loads KEY=VALUE pairs from .env without overriding existing values."""
  if not path.is_file():
    return
  for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip())


_load_env_file(Path(__file__).resolve().parent.parent / ".env")


def _element_to_dict(aw_index: int, el) -> Dict[str, Any]:
  """Converts an AndroidWorld UIElement to jev-mobile's canonical schema."""
  bbox = el.bbox_pixels
  bounds = ""
  if bbox is not None:
    bounds = "%d,%d,%d,%d" % (
        int(bbox.x_min), int(bbox.y_min), int(bbox.x_max), int(bbox.y_max)
    )
  return {
      "aw_index": aw_index,
      "className": el.class_name or "",
      "package": el.package_name or "",
      "resourceId": el.resource_id or "",
      "text": el.text or "",
      "contentDescription": el.content_description or "",
      "bounds": bounds,
      "clickable": bool(el.is_clickable),
      "enabled": el.is_enabled is None or bool(el.is_enabled),
      "scrollable": bool(el.is_scrollable),
      "checked": bool(el.is_checked),
      "selected": bool(el.is_selected),
      "editable": bool(el.is_editable),
      "children": [],
  }


class JevMobileAgent(base_agent.EnvironmentInteractingAgent):
  """jev-mobile policy running inside the AndroidWorld evaluation loop."""

  def __init__(
      self,
      env,
      name: str = "JevMobileAgent",
      verbose: bool = False,
  ):
    super().__init__(env, name)
    self._verbose = verbose
    self._reset_state()

  # -- state ------------------------------------------------------------

  def _reset_state(self) -> None:
    self._goal: Optional[str] = None
    self._history: List[Dict[str, Any]] = []
    self._done_vetoes = 0
    self._steps = 0
    self._model_errors = 0
    self._usage = {
        "jev_requests": 0,
        "text_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    self._decision_log: List[Dict[str, Any]] = []
    self._last_fingerprint: Optional[str] = None
    self._visible: List[Dict[str, Any]] = []

  def reset(self, go_home: bool = False) -> None:
    self._reset_state()
    super().reset(go_home=go_home)

  # -- helpers ----------------------------------------------------------

  def _foreground_meta(self) -> Dict[str, str]:
    try:
      activity = self.env.foreground_activity_name or ""
    except Exception:
      activity = ""
    app = activity.split("/")[0] if activity else ""
    return {"app": app, "activity": activity}

  def _filter_stale_windows(
      self, tree: List[Dict[str, Any]], app: str
  ) -> List[Dict[str, Any]]:
    """Drops elements from windows of apps that are no longer foreground.

    The persistent a11y window cache keeps recently-exited apps' trees alive
    briefly; without this filter their elements pollute the action table and
    Jev taps phantom controls.
    """
    if not app or not tree:
      return tree
    if not any(t.get("package") == app for t in tree):
      return tree  # Foreground app reports no window yet; keep everything.
    keep = SYSTEM_PACKAGES | {app}
    return [t for t in tree if t.get("package") in keep]

  def _observe(self, state) -> Dict[str, Any]:
    """Builds the jev-mobile page dict from AndroidWorld ui_elements.

    Retries the fetch when the observation is unusable: an empty tree, or a
    tree with zero interactive actions right after a screen transition (the
    new foreground window's a11y push may not have arrived yet). An empty
    observation must never reach Jev as a "nothing is possible" screen,
    because it produces an immediate, spurious BLOCKED.
    """
    page = None
    for attempt in range(OBSERVE_RETRIES):
      screen = self.env.device_screen_size
      meta = self._foreground_meta()
      tree = [
          _element_to_dict(i, el) for i, el in enumerate(state.ui_elements)
      ]
      tree = self._filter_stale_windows(tree, meta.get("app", ""))
      normalized = normalize_tree(tree)
      # snapshot_state enumerates _visible_nodes(_flatten(elements)) to
      # assign node ids; recompute the same list so node -> aw_index stays
      # exact.
      self._visible = _visible_nodes(_flatten(normalized), screen)
      page = snapshot_state(normalized, meta, "android_world", screen)
      # Jev cannot launch apps through the fixed control set; without this it
      # stalls on the home screen for app-centric goals (e.g. settings tasks).
      page["actions"].append({
          "id": "open_app",
          "kind": "open_app",
          "label": "OPEN_APP",
          "description": (
              "Launch the app the goal requires. System or device goals --"
              " wifi, bluetooth, brightness, airplane mode, sound, display,"
              " battery -- are always done inside the Settings app, never"
              " BLOCKED from the home screen. Clock/stopwatch: Clock. Contacts:"
              " Contacts. Texting: the SMS messenger app. A helper supplies"
              " the exact app name."
          ),
      })
      interactive = [a for a in page["actions"] if a["kind"] in {"click", "fill"}]
      # Probe: capture the observation pipeline's intermediate sizes so
      # "zero interactive actions" failures can be attributed post-hoc.
      self._last_observe_stats = {
          "attempt": attempt,
          "screen": list(screen),
          "n_state_elements": len(state.ui_elements),
          "n_visible": len(self._visible),
          "n_actions": len(page["actions"]),
          "n_interactive": len(interactive),
          "app": page.get("app", ""),
      }
      if interactive or attempt == OBSERVE_RETRIES - 1:
        return page
      time.sleep(1.0)
      state = self.get_post_transition_state()
    return page

  def _aw_index(self, action: Dict[str, Any]) -> Optional[int]:
    node = action.get("node")
    if node is None or not (0 <= node < len(self._visible)):
      return None
    return self._visible[node].get("aw_index")

  def _to_json_action(
      self, action: Dict[str, Any], text: Optional[str]
  ) -> json_action.JSONAction:
    kind = action["kind"]
    if kind == "click":
      index = self._aw_index(action)
      if index is not None:
        return json_action.JSONAction(
            action_type=json_action.CLICK, index=index
        )
      return json_action.JSONAction(
          action_type=json_action.CLICK,
          x=action["center"][0],
          y=action["center"][1],
      )
    if kind == "fill":
      index = self._aw_index(action)
      kwargs = {"text": text or ""}
      if index is not None:
        kwargs["index"] = index
      else:
        kwargs["x"] = action["center"][0]
        kwargs["y"] = action["center"][1]
      return json_action.JSONAction(
          action_type=json_action.INPUT_TEXT,
          clear_text=True,
          **kwargs,
      )
    if kind == "open_app":
      return json_action.JSONAction(
          action_type=json_action.OPEN_APP,
          app_name=(text or "").strip().lower(),
      )
    if kind == "scroll":
      return json_action.JSONAction(
          action_type=json_action.SCROLL, direction=action["direction"]
      )
    if kind == "home":
      return json_action.JSONAction(action_type=json_action.NAVIGATE_HOME)
    if kind == "key":
      # Their key controls in this bridge are only back (4) and enter (66).
      return json_action.JSONAction(
          action_type=(
              json_action.NAVIGATE_BACK
              if action.get("keycode") == 4
              else json_action.KEYBOARD_ENTER
          )
      )
    return json_action.JSONAction(action_type=json_action.WAIT)

  def _record_usage(self, decision: Dict[str, Any]) -> None:
    usage = decision.get("usage") or {}
    self._usage["jev_requests"] += 1
    self._usage["input_tokens"] += usage.get("input_tokens", 0) or 0
    self._usage["output_tokens"] += usage.get("output_tokens", 0) or 0

  def _history_entry(
      self,
      action: Dict[str, Any],
      text: Optional[str],
      decision: Dict[str, Any],
      page: Dict[str, Any],
      helper: Optional[Dict[str, Any]],
  ) -> Dict[str, Any]:
    return {
        "step": len(self._history) + 1,
        "action": action["label"],
        "kind": action["kind"],
        "choice": decision["choice"],
        "text": text,
        "operation": decision["operation"],
        "target": decision["target"],
        "confidence": decision["confidence"],
        "latency_ms": decision["latency_ms"],
        "page_changed": (
            None
            if self._last_fingerprint is None
            else page["fingerprint"] != self._last_fingerprint
        ),
    }

  # -- the agent step ----------------------------------------------------

  def step(self, goal: str) -> base_agent.AgentInteractionResult:
    # run.py creates one agent for the whole suite; a changed goal marks a
    # new task, so per-task state resets here.
    if goal != self._goal:
      self._reset_state()
      self._goal = goal
      # A new task starts on a fresh screen; cached windows from the previous
      # task's apps would pollute the merged a11y forest.
      android_world_controller.clear_a11y_window_cache()

    state = self.get_post_transition_state()
    page = self._observe(state)

    step_data: Dict[str, Any] = {
        "raw_screenshot": state.pixels,
        "ui_elements": state.ui_elements,
        "steps": self._steps,
        "observe_stats": dict(getattr(self, "_last_observe_stats", {})),
    }

    # -- decide ----------------------------------------------------------
    try:
      decision = choose(page, goal, self._history)
      self._model_errors = 0
      self._record_usage(decision)
    except Exception as e:  # Model/provider failure: wait, do not act blind.
      self._model_errors += 1
      step_data["error"] = f"choose failed: {e}"
      self.env.execute_action(
          json_action.JSONAction(action_type=json_action.WAIT)
      )
      done = self._model_errors >= MAX_MODEL_ERRORS
      if done:
        step_data["status"] = "model_error"
      return base_agent.AgentInteractionResult(done, step_data)

    step_data["jev"] = {
        k: decision.get(k)
        for k in (
            "operation",
            "target",
            "choice",
            "confidence",
            "probabilities",
            "operation_probabilities",
            "target_probabilities",
            "target_confidence",
            "goal",
            "latency_ms",
            "model",
            "usage",
        )
    }
    self._decision_log.append(step_data["jev"])

    gate = decision.get("goal") or {}
    operation = decision["operation"]

    # -- goal gate can end the run even when the head wanted another action --
    if gate.get("satisfied") is True:
      step_data["status"] = "done_by_goal_gate"
      return base_agent.AgentInteractionResult(True, step_data)

    # -- DONE veto policy (mirrors jev_mobile.agent) -----------------------
    if (
        operation == "DONE"
        and gate.get("satisfied") is False
        and self._done_vetoes < MAX_DONE_VETOES
    ):
      self._done_vetoes += 1
      step_data["status"] = "done_vetoed"
      self.env.execute_action(
          json_action.JSONAction(action_type=json_action.WAIT)
      )
      self._history.append(
          self._history_entry(
              next(a for a in page["actions"] if a["id"] == "wait"),
              None,
              decision,
              page,
              None,
          )
      )
      self._steps += 1
      self._last_fingerprint = page["fingerprint"]
      step_data["steps"] = self._steps
      return base_agent.AgentInteractionResult(False, step_data)

    # -- termination --------------------------------------------------------
    if operation in {"DONE", "BLOCKED"}:
      step_data["status"] = operation.lower()
      step_data["usage"] = self._usage
      step_data["decisions"] = self._decision_log
      return base_agent.AgentInteractionResult(True, step_data)

    if self._steps >= MAX_STEPS:
      step_data["status"] = "step_budget"
      step_data["usage"] = self._usage
      step_data["decisions"] = self._decision_log
      return base_agent.AgentInteractionResult(True, step_data)

    # -- act ---------------------------------------------------------------
    selected = decision["choice"]
    action = next(
        (a for a in page["actions"] if a["id"] == selected), None
    )
    if action is None:
      # Choice id no longer present (stale snapshot): wait one round.
      step_data["status"] = "stale_choice"
      self.env.execute_action(
          json_action.JSONAction(action_type=json_action.WAIT)
      )
      return base_agent.AgentInteractionResult(False, step_data)

    text = None
    helper = None
    if action["kind"] == "open_app":
      context = {
          "goal": goal,
          "field": {
              "label": "app to launch",
              "role": "Android app name, lowercase (settings, contacts,"
                      " clock, messages, vlc...)",
              "value": "",
          },
      }
      try:
        text, helper = field_text(context)
      except ValueError as e:
        # A flaky helper response must not kill the whole task; wait and let
        # Jev re-decide next step.
        step_data["error"] = f"open_app text helper failed: {e}"
        self.env.execute_action(
            json_action.JSONAction(action_type=json_action.WAIT)
        )
        return base_agent.AgentInteractionResult(False, step_data)
      self._usage["text_requests"] += 1
      text_usage = helper.get("usage") or {}
      self._usage["input_tokens"] += (
          text_usage.get("prompt_tokens", text_usage.get("input_tokens", 0))
          or 0
      )
      self._usage["output_tokens"] += (
          text_usage.get(
              "completion_tokens", text_usage.get("output_tokens", 0)
          )
          or 0
      )
      step_data["text"] = text
    elif action["kind"] == "fill":
      context = field_context(goal, action, page, self._history)
      try:
        text, helper = field_text(context)
      except ValueError as e:
        # A flaky helper response must not kill the whole task; wait and let
        # Jev re-decide next step.
        step_data["error"] = f"fill text helper failed: {e}"
        self.env.execute_action(
            json_action.JSONAction(action_type=json_action.WAIT)
        )
        return base_agent.AgentInteractionResult(False, step_data)
      self._usage["text_requests"] += 1
      text_usage = helper.get("usage") or {}
      self._usage["input_tokens"] += (
          text_usage.get("prompt_tokens", text_usage.get("input_tokens", 0))
          or 0
      )
      self._usage["output_tokens"] += (
          text_usage.get(
              "completion_tokens", text_usage.get("output_tokens", 0)
          )
          or 0
      )
      step_data["text"] = text

    json_act = self._to_json_action(action, text)
    step_data["action"] = json_act.as_dict()
    self.env.execute_action(json_act)
    # Let launch/scroll animations settle before the next step observes.
    if action["kind"] in {"click", "fill", "scroll", "open_app"}:
      time.sleep(SETTLE_SECONDS)

    self._history.append(
        self._history_entry(action, text, decision, page, helper)
    )
    self._steps += 1
    self._last_fingerprint = page["fingerprint"]
    step_data["steps"] = self._steps
    step_data["usage"] = self._usage

    if self._verbose:
      print(
          "[jev-mobile] step %d %s -> %s (conf=%.2f, gate=%s)"
          % (
              self._steps,
              operation,
              step_data["action"],
              decision["confidence"] or 0.0,
              gate.get("satisfied"),
          )
      )

    return base_agent.AgentInteractionResult(False, step_data)
