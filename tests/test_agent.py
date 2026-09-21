"""Agent-loop tests with a fake device and scripted decisions."""

import pytest

import jev_mobile.agent as agent_module
from jev_mobile.agent import Agent

from helpers import FakeDevice, make_decision, node


def install_script(monkeypatch, decisions):
    queue = list(decisions)
    pages = []

    def fake_choose(page, goal, history):
        pages.append(page)
        operation, target = queue.pop(0)
        return make_decision(operation, target, page)

    monkeypatch.setattr(agent_module, "choose", fake_choose)
    return pages


def install_field_text(monkeypatch, value="hello"):
    monkeypatch.setattr(
        agent_module,
        "field_text",
        lambda context: (value, {"model": "fake-text", "latency_ms": 5, "usage": {}}),
    )


def simple_tree():
    return [node(text="Continue", clickable=True)]


class TestBasicLoop:
    def test_click_then_done(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("finish the flow", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "done"
        assert len(agent.state["history"]) == 1
        assert device.acts[0][0]["kind"] == "click"
        assert agent.state["history"][0]["page_changed"] is False

    def test_page_change_is_recorded(self, monkeypatch):
        device = FakeDevice([simple_tree(), [node(text="Result", clickable=True)]])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("finish the flow", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["history"][0]["page_changed"] is True

    def test_blocked_status(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("BLOCKED", None)])
        agent = Agent("impossible task", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "blocked"

    def test_trace_records_usage_totals(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("finish the flow", device=device)
        for _ in agent.run():
            pass
        agent.close()
        usage = agent.trace()["usage"]
        assert usage["decision"]["requests"] == 2
        assert usage["text"]["requests"] == 0
        assert usage["total"]["input_tokens"] == 0  # fake decisions carry no usage


class TestTyping:
    def test_type_text_calls_helper_and_executes_fill(self, monkeypatch):
        tree = [node(cls="android.widget.EditText", rid="com.example:id/query", text="", clickable=False)]
        device = FakeDevice([tree])
        install_script(monkeypatch, [("TYPE_TEXT", "1"), ("DONE", None)])
        install_field_text(monkeypatch, "the query")
        agent = Agent("search for a widget", device=device)
        for _ in agent.run():
            pass
        agent.close()
        action, text = device.acts[0]
        assert action["kind"] == "fill"
        assert action["label"] == "query"
        assert text == "the query"
        assert agent.state["text_calls"][0]["value"] == "the query"
        assert agent.state["history"][0]["text"] == "the query"


class TestStaleness:
    def test_stale_done_reobserves_and_decides_again(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        device.fresh_results = [False, True]
        pages = install_script(monkeypatch, [("DONE", None), ("DONE", None)])
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "done"
        assert len(pages) == 2  # the stale decision was discarded and re-predicted

    def test_activity_change_reobserves_before_predicting(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        device.activity_changed_result = True
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        # init + re-observe before each of the two predictions (focus keeps differing) + post-action
        assert device.observe_count == 4


class TestGoalGate:
    def install_script_with_gates(self, monkeypatch, decisions):
        queue = list(decisions)

        def fake_choose(page, goal, history):
            operation, target, gate = queue.pop(0)
            return make_decision(operation, target, page, goal=gate)

        monkeypatch.setattr(agent_module, "choose", fake_choose)

    def test_gate_ends_run_when_operation_wants_to_continue(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        self.install_script_with_gates(monkeypatch, [("CLICK", "1", {"satisfied": True, "probability": 0.95})])
        agent = Agent("finish the flow", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "done"
        assert device.acts == []  # the gate fired before the click executed
        assert "goal_done" in [e["type"] for e in agent.state["events"]]

    def test_gate_vetoes_done_waits_then_accepts_persistent_done(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        no = {"satisfied": False, "probability": 0.1}
        self.install_script_with_gates(monkeypatch, [("DONE", None, no), ("DONE", None, no), ("DONE", None, no)])
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        # Two vetoes (each running the built-in WAIT), then the third DONE wins.
        assert agent.state["status"] == "done"
        assert agent.state["done_vetoes"] == 2
        assert [h["kind"] for h in agent.state["history"]] == ["wait", "wait"]
        kinds = [e["type"] for e in agent.state["events"]]
        assert kinds.count("done_vetoed") == 2

    def test_veto_counter_resets_on_non_done_decision(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        no = {"satisfied": False, "probability": 0.1}
        self.install_script_with_gates(
            monkeypatch,
            [("DONE", None, no), ("CLICK", "1", no), ("DONE", None, no), ("DONE", None, no), ("DONE", None, no)],
        )
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "done"
        # veto → the CLICK resets the counter → two more vetoes before the DONE is accepted.
        assert kinds_count(agent, "done_vetoed") == 3
        assert [h["kind"] for h in agent.state["history"]] == ["wait", "click", "wait", "wait"]

    def test_gate_termination_still_requires_freshness(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        device.fresh_results = [False, True]
        yes = {"satisfied": True, "probability": 0.9}
        self.install_script_with_gates(monkeypatch, [("CLICK", "1", yes), ("DONE", None, yes)])
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "done"
        assert "stale_done" in [e["type"] for e in agent.state["events"]]


def kinds_count(agent, kind):
    return [e["type"] for e in agent.state["events"]].count(kind)


class TestBudgets:
    def test_three_unchanged_actions_block(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("CLICK", "1"), ("CLICK", "1")])
        agent = Agent("stuck flow", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.state["status"] == "blocked"
        assert len(agent.state["history"]) == 3

    def test_action_budget_stops_the_run(self, monkeypatch):
        monkeypatch.setattr(agent_module, "MAX_STEPS", 2)
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1")] * 3)
        agent = Agent("long flow", device=device)
        with pytest.raises(ValueError):
            for _ in agent.run():
                pass
        agent.close()
        assert agent.state["status"] == "blocked"


class TestEvents:
    def test_decision_events_match_decisions(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        decisions = [e for e in agent.state["events"] if e["type"] == "decision"]
        assert len(decisions) == len(agent.state["decisions"]) == 2
        assert decisions[0]["operation"] == "CLICK"
        assert "probabilities" not in decisions[0]  # lightweight timeline, not the full record
        assert decisions[0]["operation_probabilities"]

    def test_stale_done_is_recorded_as_event(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        device.fresh_results = [False, True]
        install_script(monkeypatch, [("DONE", None), ("DONE", None)])
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        kinds = [e["type"] for e in agent.state["events"]]
        assert "stale_done" in kinds
        assert agent.state["status"] == "done"

    def test_stuck_is_recorded_as_event(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1")] * 3)
        agent = Agent("stuck flow", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert [e["type"] for e in agent.state["events"]][-1] == "stuck"

    def test_history_keeps_activity_transition(self, monkeypatch):
        device = FakeDevice([simple_tree(), [node(text="Result", clickable=True)]])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("navigate", device=device)
        for _ in agent.run():
            pass
        agent.close()
        # Both observations report the same fake activity; no transition expected.
        step = agent.state["history"][0]
        assert step["activity_before"] == step["activity"]


class TestConstruction:
    def test_empty_task_is_rejected(self):
        with pytest.raises(ValueError):
            Agent("   ")

    def test_negative_action_interval_is_rejected(self):
        with pytest.raises(ValueError):
            Agent("task", device=FakeDevice([simple_tree()]), action_interval=-1)

    def test_action_interval_waits_after_each_action(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        sleeps = []
        monkeypatch.setattr(agent_module.time, "sleep", lambda s: sleeps.append(s))
        agent = Agent("finish", device=device, action_interval=1.5)
        for _ in agent.run():
            pass
        agent.close()
        assert 1.5 in sleeps  # one executed action -> one interval wait
        assert agent.state["status"] == "done"

    def test_no_interval_by_default(self, monkeypatch):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        sleeps = []
        monkeypatch.setattr(agent_module.time, "sleep", lambda s: sleeps.append(s))
        agent = Agent("finish", device=device)
        for _ in agent.run():
            pass
        agent.close()
        assert sleeps == []

    def test_start_package_launches_before_first_observation(self):
        device = FakeDevice([simple_tree()])
        Agent("open something", device=device, start_package="com.example.app")
        assert device.acts[0] == ({"id": "launch", "kind": "launch"}, "com.example.app")

    def test_record_without_screenshots_keeps_trace_only(self, monkeypatch, tmp_path):
        device = FakeDevice([simple_tree()])
        install_script(monkeypatch, [("CLICK", "1"), ("DONE", None)])
        agent = Agent("finish", device=device, record_dir=str(tmp_path / "run"), screenshots=False)
        for _ in agent.run():
            pass
        agent.close()
        assert agent.record is True and agent.screenshots is False
        assert not (tmp_path / "run" / "000000.jpg").exists()

    def test_recording_default_still_implies_screenshots(self):
        device = FakeDevice([simple_tree()])
        agent = Agent("finish", device=device, record_dir=None, screenshots=None)
        agent.close()
        assert agent.screenshots is False  # nothing recorded without a record dir
