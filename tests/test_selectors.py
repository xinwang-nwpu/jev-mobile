"""Exact selector matching across the entire captured A11Y tree."""

import copy

import pytest

from jev_mobile.selectors import SELECTOR_FIELDS, annotate_duplicates, find_matches
from helpers import node


def test_duplicates_ignore_geometry_flags_and_parent_but_keep_every_node():
    first = node(rid="com.example:id/icon", text="", desc="shortcut", clickable=True,
                 bounds="[0,100][100,200]")
    second = {**first, "bounds": "[200,300][300,400]", "enabled": False, "isVisibleToUser": False}
    tree = [node(cls="Root", children=[first, node(cls="Container", children=[second])])]
    original = copy.deepcopy(tree)
    report = annotate_duplicates(tree)
    assert tree == original
    assert report["node_count"] == 4
    assert report["duplicate_group_count"] == 1 and report["duplicate_node_count"] == 2
    assert report["duplicate_groups"] == [{"selector": {field: first.get(field) for field in SELECTOR_FIELDS},
                                            "match_count": 2}]
    saved_first, container = report["tree"][0]["children"]
    saved_second = container["children"][0]
    assert saved_first["is_duplicate"] and saved_second["is_duplicate"]
    assert saved_first["match_count"] == saved_second["match_count"] == 2
    assert container["is_duplicate"] is False and container["match_count"] == 1
    assert find_matches(tree, report["duplicate_groups"][0]["selector"]) == [first, second]


@pytest.mark.parametrize("field", SELECTOR_FIELDS)
def test_a_difference_in_any_of_the_four_fields_prevents_a_duplicate(field):
    first = node(rid="id", cls="ImageView", text="name", desc="description")
    second = {**first, field: first[field] + " "}
    report = annotate_duplicates([first, second])
    assert report["duplicate_node_count"] == report["duplicate_group_count"] == 0
    selector = {key: first[key] for key in SELECTOR_FIELDS}
    assert find_matches([first, second], selector) == [first]


def test_missing_null_and_empty_values_are_compared_without_string_coercion():
    report = annotate_duplicates([{}, {field: None for field in SELECTOR_FIELDS},
                                  {field: "" for field in SELECTOR_FIELDS}])
    assert report["duplicate_group_count"] == 1 and report["duplicate_node_count"] == 2
    assert [n["match_count"] for n in report["tree"]] == [2, 2, 1]


def test_duplicate_scan_is_not_limited_to_model_candidates():
    report = annotate_duplicates([node(cls="Root", children=[node(text="same") for _ in range(300)])])
    assert report["node_count"] == 301 and report["duplicate_node_count"] == 300
    assert all(n["match_count"] == 300 for n in report["tree"][0]["children"])
    assert annotate_duplicates([])["node_count"] == 0


def test_selector_requires_exactly_the_declared_fields():
    with pytest.raises(ValueError, match="exactly"):
        find_matches([], {"resourceId": "id"})
    with pytest.raises(ValueError, match="exactly"):
        find_matches([], {**dict.fromkeys(SELECTOR_FIELDS, ""), "bounds": "[0,0][1,1]"})
