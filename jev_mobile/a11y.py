"""One A11Y snapshot becomes an indexed action space with code-owned ids.

Every observation flattens the accessibility tree, keeps visible nodes, and derives
click/fill actions plus fixed device controls. A node keeps one id even when it supports
both tapping and typing. Ids are positions in this snapshot only; the executor resolves
them to coordinates from the observed bounds immediately before input.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_ELEMENTS = 250
MAX_TEXT_CHARS = 6000
MAX_LABEL_CHARS = 80
EDITABLE_CLASS_HINTS = ("EditText", "AutoCompleteTextView")

PORTAL_STATE_URIS = (
    ("mobilerun_portal", "content://com.mobilerun.portal/state_full"),
    ("mobilerun_portal", "content://com.mobilerun.portal/state"),
    ("droidrun_portal", "content://com.droidrun.portal/state_full"),
    ("droidrun_portal", "content://com.droidrun.portal/state"),
)

CONTROL_LABELS = {
    "SCROLL_DOWN": "Scroll the main scrollable container down to reveal content below.",
    "SCROLL_UP": "Scroll the main scrollable container back up.",
    "BACK": "Press Back to dismiss the keyboard, a dialog, or an overlay.",
    "HOME": "Press Home to leave the current app.",
    "ENTER": "Press Enter to submit the focused field or confirm a choice.",
    "WAIT": "Wait briefly while the screen loads or animates.",
}


def snapshot_state(
    elements: Sequence[Dict[str, Any]],
    meta: Dict[str, str],
    source: str,
    screen: Tuple[int, int],
) -> Dict[str, Any]:
    """Build the observed state: actions, page text, and a semantic fingerprint."""
    visible = _visible_nodes(_flatten(elements), screen)
    actions: List[Dict[str, Any]] = []
    texts: List[str] = []
    anchor: Optional[List[int]] = None
    anchor_area = 0
    for node_id, el in enumerate(visible):
        bounds = parse_bounds(el.get("bounds"))
        if not bounds:
            continue
        center = [(bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2]
        if _flag(el, "scrollable"):
            area = max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])
            if area > anchor_area:
                anchor, anchor_area = center, area
        for value in (el.get("text"), el.get("contentDescription")):
            text = str(value or "").strip()
            if text and (not texts or texts[-1] != text):
                texts.append(text)
        if not (_flag(el, "clickable") or _editable(el)):
            continue
        if not _flag(el, "enabled", True):
            continue
        role = _short_class(el.get("className"))
        label = _label(el)
        base = {
            "node": node_id,
            "label": label,
            "role": role,
            "center": center,
            "bounds": ",".join(str(v) for v in bounds),
        }
        if _flag(el, "checked") or _flag(el, "selected"):
            base["checked"] = True
        if _flag(el, "clickable"):
            actions.append({"id": "tap-%d" % node_id, "kind": "click", **base})
        if _editable(el):
            actions.append({"id": "type-%d" % node_id, "kind": "fill", "value": str(el.get("text") or ""), **base})
    actions.extend(_control_actions(anchor, screen))
    state = {
        "source": source,
        "app": meta.get("app", ""),
        "activity": meta.get("activity", ""),
        "text": "\n".join(texts)[:MAX_TEXT_CHARS],
        "actions": actions,
        "screen": list(screen),
    }
    state["fingerprint"] = fingerprint(state)
    return state


def _control_actions(anchor: Optional[List[int]], screen: Tuple[int, int]) -> List[Dict[str, Any]]:
    scroll_center = anchor or [screen[0] // 2, screen[1] // 2]
    controls = [
        ("scroll_down", "scroll", CONTROL_LABELS["SCROLL_DOWN"], {"direction": "down", "center": scroll_center}),
        ("scroll_up", "scroll", CONTROL_LABELS["SCROLL_UP"], {"direction": "up", "center": scroll_center}),
        ("back", "key", CONTROL_LABELS["BACK"], {"keycode": 4}),
        ("home", "home", CONTROL_LABELS["HOME"], {}),
        ("enter", "key", CONTROL_LABELS["ENTER"], {"keycode": 66}),
        ("wait", "wait", CONTROL_LABELS["WAIT"], {}),
    ]
    return [
        {"id": cid, "kind": kind, "label": cid.upper(), "description": description, **extra}
        for cid, kind, description, extra in controls
    ]


def fingerprint(state: Dict[str, Any]) -> str:
    content = {
        "activity": state["activity"],
        "app": state["app"],
        "text": state["text"],
        "actions": [
            {k: a[k] for k in ("id", "label", "value", "checked", "center", "bounds") if k in a}
            for a in state["actions"]
        ],
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def parse_uiautomator_xml(xml_text: str) -> List[Dict[str, Any]]:
    start = xml_text.find("<?xml")
    if start > 0:
        xml_text = xml_text[start:]
    root = ET.fromstring(xml_text)

    def convert(node: ET.Element) -> Dict[str, Any]:
        item = {
            "className": node.attrib.get("class", ""),
            "resourceId": node.attrib.get("resource-id", ""),
            "text": node.attrib.get("text", ""),
            "contentDescription": node.attrib.get("content-desc", ""),
            "bounds": node.attrib.get("bounds", ""),
            "clickable": _truthy(node.attrib.get("clickable")),
            "enabled": _truthy(node.attrib.get("enabled")),
            "scrollable": _truthy(node.attrib.get("scrollable")),
            "checked": _truthy(node.attrib.get("checked")),
            "selected": _truthy(node.attrib.get("selected")),
            "children": [],
        }
        item["children"] = [convert(child) for child in list(node) if child.tag == "node"]
        return item

    return [convert(child) for child in list(root) if child.tag == "node"]


# Full-format portal nodes carry is*-prefixed flags and dict bounds; map them to the
# canonical schema shared with uiautomator XML.
FULL_FLAG_MAP = {
    "isClickable": "clickable",
    "isEnabled": "enabled",
    "isEditable": "editable",
    "isScrollable": "scrollable",
    "isChecked": "checked",
    "isSelected": "selected",
    "isFocusable": "focusable",
    "isLongClickable": "longClickable",
}


def normalize_tree(elements: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        item = dict(element)
        if "boundsInScreen" in item and "bounds" not in item:
            item["bounds"] = item.pop("boundsInScreen")
        for source_key, target_key in FULL_FLAG_MAP.items():
            if source_key in item:
                item[target_key] = item.pop(source_key)
        if "resourceId" not in item and "resource-id" in item:
            item["resourceId"] = item.pop("resource-id")
        if "contentDescription" not in item and "content-desc" in item:
            item["contentDescription"] = item.pop("content-desc")
        if "className" not in item and "class" in item:
            item["className"] = item.pop("class")
        bounds = parse_bounds(item.get("bounds"))
        item["bounds"] = ",".join(str(v) for v in bounds) if bounds else ""
        children = item.get("children")
        if isinstance(children, list):
            item["children"] = normalize_tree(children)
        normalized.append(item)
    return normalized


def parse_content_provider_output(raw_output: str) -> Optional[Dict[str, Any]]:
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "result=" in line:
            line = line.split("result=", 1)[1].strip()
        if line.startswith("{") or line.startswith("["):
            try:
                parsed = json.loads(line)
                return parsed if isinstance(parsed, dict) else {"data": parsed}
            except json.JSONDecodeError:
                continue
    return None


def parse_bounds(value: Any) -> Optional[Tuple[int, int, int, int]]:
    """Parse 'l,t,r,b', '[l,t][r,b]', list, tuple, or dict bounds."""
    if value in ("", None):
        return None
    if isinstance(value, dict):
        keys = ("left", "top", "right", "bottom")
        if all(k in value for k in keys):
            return tuple(int(value[k]) for k in keys)  # type: ignore[return-value]
        keys2 = ("x", "y", "width", "height")
        if all(k in value for k in keys2):
            x, y, w, h = (int(value[k]) for k in keys2)
            return x, y, x + w, y + h
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(int(v) for v in value)  # type: ignore[return-value]
    nums = [int(n) for n in re.findall(r"-?\d+", str(value).strip())]
    if len(nums) >= 4:
        return nums[0], nums[1], nums[2], nums[3]
    return None


def _flatten(elements: Iterable[Any]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []

    def visit(node: Any) -> None:
        if len(flat) >= MAX_ELEMENTS or not isinstance(node, dict):
            return
        flat.append({k: v for k, v in node.items() if k != "children"})
        for child in node.get("children") or []:
            visit(child)

    for element in elements or []:
        visit(element)
    return flat


def _visible_nodes(nodes: Sequence[Dict[str, Any]], screen: Tuple[int, int]) -> List[Dict[str, Any]]:
    sw, sh = screen
    out = []
    for el in nodes:
        bounds = parse_bounds(el.get("bounds"))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if right - left < 2 or bottom - top < 2:
            continue
        if left >= sw or top >= sh or right <= 0 or bottom <= 0:
            continue
        out.append(el)
    return out


def _editable(el: Dict[str, Any]) -> bool:
    raw = el.get("editable")
    if raw is not None and raw != "":
        return _truthy(raw)
    cls = str(el.get("className") or "")
    return any(hint in cls for hint in EDITABLE_CLASS_HINTS)


def _label(el: Dict[str, Any]) -> str:
    desc = str(el.get("contentDescription") or "").strip()
    if _editable(el):
        source = desc or _short_resource(el.get("resourceId"))
    else:
        source = str(el.get("text") or "").strip() or desc or _short_resource(el.get("resourceId"))
    return (source or _short_class(el.get("className")))[:MAX_LABEL_CHARS]


def _short_resource(resource_id: Any) -> str:
    text = str(resource_id or "").strip()
    return text.rsplit("id/", 1)[-1] if "id/" in text else ""


def _short_class(class_name: Any) -> str:
    text = str(class_name or "")
    return text.rsplit(".", 1)[-1] if text else "View"


def _flag(el: Dict[str, Any], name: str, default: bool = False) -> bool:
    value = el.get(name, default)
    if value is None or value == "":
        return default
    return _truthy(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}
