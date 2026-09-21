"""A11Y snapshot tests: tree parsing, action derivation, fingerprints."""

from jev_mobile.a11y import (
    fingerprint,
    normalize_tree,
    parse_bounds,
    parse_uiautomator_xml,
    snapshot_state,
)

from helpers import make_page, node

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="com.example:id/root" class="android.widget.FrameLayout"
        package="com.example" content-desc="" checkable="false" checked="false" clickable="false"
        enabled="true" focusable="false" focused="false" scrollable="false"
        bounds="[0,0][1080,2340]">
    <node index="1" text="Search" resource-id="com.example:id/search" class="android.widget.EditText"
          package="com.example" content-desc="" checked="false" clickable="true" enabled="true"
          scrollable="false" bounds="[0,100][400,200]"/>
  </node>
</hierarchy>"""


def test_parse_uiautomator_xml_normalizes_attributes():
    tree = parse_uiautomator_xml(SAMPLE_XML)
    assert len(tree) == 1
    child = tree[0]["children"][0]
    assert child["resourceId"] == "com.example:id/search"
    assert child["contentDescription"] == ""
    assert child["clickable"] is True
    assert child["className"] == "android.widget.EditText"


def test_normalize_tree_maps_kebab_keys():
    tree = normalize_tree([{"class": "x", "resource-id": "y", "content-desc": "z", "bounds": "[1,2][3,4]"}])
    assert tree[0]["className"] == "x"
    assert tree[0]["resourceId"] == "y"
    assert tree[0]["contentDescription"] == "z"
    assert tree[0]["bounds"] == "1,2,3,4"


def test_normalize_tree_maps_full_format_portal_nodes():
    full_node = {
        "className": "android.widget.TextView",
        "text": "Go",
        "isClickable": True,
        "isEnabled": True,
        "isEditable": False,
        "isScrollable": False,
        "isChecked": False,
        "isSelected": False,
        "boundsInScreen": {"left": 10, "top": 20, "right": 110, "bottom": 220},
        "children": [],
    }
    tree = normalize_tree([full_node])
    node = tree[0]
    assert node["clickable"] is True
    assert node["enabled"] is True
    assert node["editable"] is False
    assert node["bounds"] == "10,20,110,220"
    assert "isClickable" not in node and "boundsInScreen" not in node


def test_full_format_nodes_drive_the_action_space():
    tree = normalize_tree([
        {
            "className": "android.widget.EditText",
            "text": "old",
            "isClickable": True,
            "isEnabled": True,
            "isEditable": True,
            "boundsInScreen": {"left": 0, "top": 100, "right": 400, "bottom": 200},
            "children": [],
        }
    ])
    state = make_page(tree)
    ids = [a["id"] for a in state["actions"]]
    assert "tap-0" in ids and "type-0" in ids


def test_parse_bounds_variants():
    assert parse_bounds("[10,20][110,220]") == (10, 20, 110, 220)
    assert parse_bounds("10,20,110,220") == (10, 20, 110, 220)
    assert parse_bounds([10, 20, 110, 220]) == (10, 20, 110, 220)
    assert parse_bounds({"x": 10, "y": 20, "width": 100, "height": 200}) == (10, 20, 110, 220)
    assert parse_bounds("") is None


def test_editable_and_clickable_share_one_node():
    tree = [node(cls="android.widget.EditText", text="old value", clickable=True)]
    state = make_page(tree)
    ids = [a["id"] for a in state["actions"]]
    assert "tap-0" in ids and "type-0" in ids
    fill = next(a for a in state["actions"] if a["id"] == "type-0")
    assert fill["kind"] == "fill"
    assert fill["value"] == "old value"


def test_editable_label_avoids_current_value():
    state = make_page([node(cls="android.widget.EditText", rid="com.example:id/query", text="typed junk", clickable=False)])
    fill = next(a for a in state["actions"] if a["kind"] == "fill")
    assert fill["label"] == "query"
    assert fill["value"] == "typed junk"


def test_offscreen_and_tiny_nodes_are_excluded():
    tree = [
        node(text="visible", clickable=True),
        node(text="offscreen", bounds="[2000,3000][2100,3100]", clickable=True),
        node(text="tiny", bounds="[10,10][11,11]", clickable=True),
    ]
    state = make_page(tree)
    labels = [a["label"] for a in state["actions"] if a["kind"] == "click"]
    assert labels == ["visible"]
    assert "offscreen" not in state["text"]


def test_control_actions_and_scroll_anchor():
    tree = [
        node(cls="android.widget.ScrollView", scrollable=True, bounds="[0,300][1080,2000]"),
        node(cls="android.widget.HorizontalScrollView", scrollable=True, bounds="[0,2100][1080,2200]"),
    ]
    state = make_page(tree)
    control_ids = {a["id"] for a in state["actions"]}
    assert {"scroll_down", "scroll_up", "back", "home", "enter", "wait"} <= control_ids
    scroll = next(a for a in state["actions"] if a["id"] == "scroll_down")
    # The largest scrollable container wins as the swipe anchor.
    assert scroll["center"] == [540, 1150]


def test_fingerprint_is_stable_and_sensitive():
    tree_a = [node(text="alpha", clickable=True)]
    tree_b = [node(text="beta", clickable=True)]
    assert fingerprint(make_page(tree_a)) == fingerprint(make_page(tree_a))
    assert fingerprint(make_page(tree_a)) != fingerprint(make_page(tree_b))


def test_page_text_joins_visible_content():
    tree = [node(text="First"), node(text="Second"), node(desc="Third")]
    state = make_page(tree)
    assert state["text"].splitlines() == ["First", "Second", "Third"]


def test_checked_state_is_kept_on_actions():
    state = make_page([node(cls="android.widget.CheckBox", text="Dark mode", clickable=True, checked=True)])
    toggle = next(a for a in state["actions"] if a["kind"] == "click")
    assert toggle["checked"] is True
