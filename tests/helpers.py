"""Shared fixtures: synthetic A11Y trees and a fake device."""

from typing import Any, Dict, List, Optional

from jev_mobile.a11y import snapshot_state
from jev_mobile.model import action_space


def node(
    cls="android.widget.TextView",
    bounds="[0,100][400,200]",
    text="",
    desc="",
    rid="",
    clickable=False,
    scrollable=False,
    checked=False,
    selected=False,
    editable=None,
    enabled=True,
    children=None,
) -> Dict[str, Any]:
    return {
        "className": cls,
        "resourceId": rid,
        "text": text,
        "contentDescription": desc,
        "bounds": bounds,
        "clickable": clickable,
        "enabled": enabled,
        "scrollable": scrollable,
        "checked": checked,
        "selected": selected,
        "editable": editable,
        "children": children or [],
    }


def make_page(tree: List[Dict[str, Any]], screen=(1080, 2340)) -> Dict[str, Any]:
    return snapshot_state(tree, {"app": "com.example", "activity": "Main"}, "fake", screen)


class FakeDevice:
    """Device double: serves scripted trees and records executed actions."""

    def __init__(self, trees: List[List[Dict[str, Any]]], screen=(1080, 2340)):
        self.trees = list(trees)
        self.screen = screen
        self.acts: List[tuple] = []
        self.observe_count = 0
        self.fresh_results: List[bool] = [True]
        self.activity_changed_result = False
        self.last_source = "uiautomator"

    def observe(self, screenshot: bool = False) -> Dict[str, Any]:
        self.observe_count += 1
        tree = self.trees.pop(0) if len(self.trees) > 1 else self.trees[0]
        state = make_page(tree, self.screen)
        if screenshot:
            state["screenshot"] = ""
        return state

    def fresh(self, page: Dict[str, Any]) -> bool:
        if len(self.fresh_results) > 1:
            return self.fresh_results.pop(0)
        return self.fresh_results[0]

    def activity_changed(self, page: Dict[str, Any]) -> bool:
        return self.activity_changed_result

    def act(self, action: Dict[str, Any], text: Optional[str] = None) -> None:
        self.acts.append((dict(action), text))

    def launch(self, package: str) -> None:
        self.acts.append(({"id": "launch", "kind": "launch"}, package))

    def close(self) -> None:
        pass


def make_decision(operation: str, target: Optional[str] = None, page: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    choice = operation
    probabilities = {operation: 1.0}
    target_probabilities = {}
    if target is not None:
        _, targets, _ = action_space(page["actions"])
        choice = targets[operation][target]["id"]
        probabilities = {choice: 1.0}
        target_probabilities = {target: 1.0}
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": 0.9,
        "probabilities": probabilities,
        "operation_probabilities": {operation: 1.0},
        "target_probabilities": target_probabilities,
        "target_probabilities": {},
        "target_confidence": 0.8 if target else None,
        "raw_answers": {},
        "model": "fake",
        "usage": {},
        "latency_ms": 10,
        "request": {},
    }
