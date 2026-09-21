"""Model-layer tests: choice validation, action space, and the TypeSafe request shape."""

import pytest

from jev_mobile import model

from helpers import make_page, node


def make_state():
    tree = [
        node(cls="android.widget.EditText", rid="com.example:id/query", text="", clickable=False),
        node(text="Go", clickable=True),
    ]
    return make_page(tree)


def all_operations(state):
    _, targets, controls = model.action_space(state["actions"])
    return {**{k: 1.0 for k in targets}, **{k: 1.0 for k in controls}, "DONE": 1.0, "BLOCKED": 1.0}


class TestValidateChoice:
    def test_accepts_valid_answer(self):
        answer = {"choice": "CLICK", "confidence": 0.9, "probabilities": {"CLICK": 0.8, "WAIT": 0.2}}
        assert model.validate_choice(answer, {"CLICK", "WAIT"})["choice"] == "CLICK"

    def test_rejects_choice_outside_ids(self):
        answer = {"choice": "SELECT", "confidence": 0.9, "probabilities": {"CLICK": 1.0}}
        with pytest.raises(ValueError):
            model.validate_choice(answer, {"CLICK"})

    def test_rejects_unnormalised_probabilities(self):
        answer = {"choice": "CLICK", "confidence": 0.9, "probabilities": {"CLICK": 0.5, "WAIT": 0.2}}
        with pytest.raises(ValueError):
            model.validate_choice(answer, {"CLICK", "WAIT"})

    def test_rejects_choice_that_is_not_the_argmax(self):
        answer = {"choice": "WAIT", "confidence": 0.9, "probabilities": {"CLICK": 0.9, "WAIT": 0.1}}
        with pytest.raises(ValueError):
            model.validate_choice(answer, {"CLICK", "WAIT"})

    def test_rejects_missing_keys(self):
        with pytest.raises(ValueError):
            model.validate_choice({"choice": "CLICK"}, {"CLICK"})


class TestActionSpace:
    def test_one_node_one_index_two_operations(self):
        state = make_page([node(cls="android.widget.EditText", text="q", clickable=True), node(text="Go", clickable=True)])
        elements, targets, controls = model.action_space(state["actions"])
        assert [e["index"] for e in elements] == ["1", "2"]
        assert elements[0]["operations"] == ["CLICK", "TYPE_TEXT"]
        assert elements[1]["operations"] == ["CLICK"]
        assert set(targets["CLICK"]) == {"1", "2"}
        assert set(targets["TYPE_TEXT"]) == {"1"}
        assert "BACK" in controls and "SCROLL_DOWN" in controls

    def test_fill_value_survives_the_click_fill_merge(self):
        # The click action comes first in the list; the fill's value must still reach the row.
        state = make_page([node(cls="android.widget.EditText", text="音乐热门歌曲", clickable=True)])
        elements, _, _ = model.action_space(state["actions"])
        assert elements[0]["operations"] == ["CLICK", "TYPE_TEXT"]
        assert elements[0]["value"] == "音乐热门歌曲"

    def test_controls_come_from_non_element_actions(self):
        state = make_state()
        _, _, controls = model.action_space(state["actions"])
        assert controls["BACK"]["kind"] == "key"
        assert controls["WAIT"]["kind"] == "wait"


class TestChoose:
    def test_one_request_decides_operation_and_targets(self, monkeypatch):
        state = make_state()
        operations = all_operations(state)
        captured = {}

        def fake_post(url, key, body):
            captured["body"] = body
            click_ids = set(body["questions"]["click_target"]["criteria"])
            type_ids = set(body["questions"]["type_text_target"]["criteria"])
            assert set(body["questions"]["operation"]["criteria"]) == set(operations)
            return {
                "model": "jev-test",
                "usage": {},
                "answers": {
                    "operation": {
                        "choice": "CLICK",
                        "confidence": 0.7,
                        "probabilities": {name: 1.0 / len(operations) for name in operations},
                    },
                    "click_target": {
                        "choice": "2",
                        "confidence": 0.8,
                        "probabilities": {name: 1.0 / len(click_ids) for name in click_ids},
                    },
                    "type_text_target": {
                        "choice": "1",
                        "confidence": 0.3,
                        "probabilities": {name: 1.0 / len(type_ids) for name in type_ids},
                    },
                    "goal_achieved": {"type": "noul", "noul": 0.9},
                },
            }

        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
        monkeypatch.setattr(model, "post_json", fake_post)
        decision = model.choose(state, "find a widget", [])
        assert decision["operation"] == "CLICK"
        assert decision["target"] == "2"
        assert decision["choice"] == "tap-1"
        assert decision["goal"] == {"satisfied": True, "probability": 0.9, "confidence": None}
        body = captured["body"]
        assert body["state"]["page"]["activity"] == "Main"
        assert body["state"]["elements"][0]["operations"]
        assert set(body["questions"]) == {"operation", "click_target", "type_text_target", "goal_achieved"}
        gate = body["questions"]["goal_achieved"]
        assert gate["type"] == "noul" and "goal" in gate["instructions"]
        criteria = body["questions"]["click_target"]["criteria"]["2"]
        assert criteria["element"].startswith("[2] Go")

    def test_control_choice_maps_to_control_id(self, monkeypatch):
        state = make_state()
        operations = all_operations(state)
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
        monkeypatch.setattr(
            model,
            "post_json",
            lambda url, key, body: {
                "model": "jev-test",
                "answers": {
                    "operation": {
                        "choice": "BACK",
                        "confidence": 0.9,
                        "probabilities": {name: 1.0 / len(operations) for name in operations},
                    }
                },
            },
        )
        decision = model.choose(state, "goal", [])
        assert decision["operation"] == "BACK"
        assert decision["choice"] == "back"


class TestValidateGoal:
    def test_majority_accepts(self):
        assert model.validate_goal({"noul": 0.6})["satisfied"] is True
        assert model.validate_goal({"noul": 0.4})["satisfied"] is False

    def test_unusable_answer_falls_back_to_operation_head(self):
        assert model.validate_goal({})["satisfied"] is None
        assert model.validate_goal({"noul": "yes"})["satisfied"] is None
        assert model.validate_goal({"noul": 1.5})["satisfied"] is None


class TestFieldText:
    def test_valid_json_value_is_returned(self, monkeypatch):
        monkeypatch.setenv("TEXT_MODEL_API_KEY", "key")
        monkeypatch.setattr(
            model,
            "post_json",
            lambda url, key, body: {"choices": [{"message": {"content": '{"text": "hello"}'}}], "usage": {}},
        )
        value, meta = model.field_text({"goal": "g"})
        assert value == "hello"
        assert meta["model"] == "deepseek-chat"

    def test_missing_key_fails_closed(self, monkeypatch):
        monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
        with pytest.raises(ValueError):
            model.field_text({"goal": "g"})

    def test_invalid_payload_fails_closed(self, monkeypatch):
        monkeypatch.setenv("TEXT_MODEL_API_KEY", "key")
        monkeypatch.setattr(
            model,
            "post_json",
            lambda url, key, body: {"choices": [{"message": {"content": '{"text": null}'}}]},
        )
        with pytest.raises(ValueError):
            model.field_text({"goal": "g"})
