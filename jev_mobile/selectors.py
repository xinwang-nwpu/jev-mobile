"""Exact four-field A11Y selectors and per-snapshot duplicate annotations."""

from collections import Counter
from copy import deepcopy

from .a11y import _flag, parse_bounds

SELECTOR_FIELDS = ("resourceId", "className", "text", "contentDescription")


def _walk(tree):
    pending = list(reversed(tree))
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        yield node
        pending.extend(reversed(node.get("children") or []))


def _identity(node):
    return tuple(node.get(field) for field in SELECTOR_FIELDS)


def _eligible(node, screen):
    if not _flag(node, "isVisibleToUser", True) or not _flag(node, "enabled", True):
        return False
    bounds = parse_bounds(node.get("bounds"))
    if not bounds:
        return False
    left, top, right, bottom = bounds
    return (right - left >= 2 and bottom - top >= 2 and right > 0 and bottom > 0
            and (screen is None or (left < screen[0] and top < screen[1])))


def find_matches(tree, selector, screen=None):
    """Match all four fields exactly; missing fields are represented by None."""
    if set(selector) != set(SELECTOR_FIELDS):
        raise ValueError("Selector must contain exactly: " + ", ".join(SELECTOR_FIELDS))
    identity = _identity(selector)
    return [node for node in _walk(tree) if _eligible(node, screen) and _identity(node) == identity]


def annotate_duplicates(tree, screen=None):
    """Retain every node and its structure, without changing the live input tree."""
    saved_tree = deepcopy(tree)
    nodes = list(_walk(saved_tree))
    eligible = [node for node in nodes if _eligible(node, screen)]
    counts = Counter(_identity(node) for node in eligible)
    for node in nodes:
        included = _eligible(node, screen)
        node["match_count"] = counts[_identity(node)] if included else None
        node["is_duplicate"] = node["match_count"] > 1 if included else None
    groups = [{"selector": dict(zip(SELECTOR_FIELDS, identity)), "match_count": count}
              for identity, count in counts.items() if count > 1]
    return {
        "selector_fields": list(SELECTOR_FIELDS),
        "node_count": len(nodes),
        "eligible_node_count": len(eligible),
        "duplicate_group_count": len(groups),
        "duplicate_node_count": sum(group["match_count"] for group in groups),
        "duplicate_groups": groups,
        "tree": saved_tree,
    }
