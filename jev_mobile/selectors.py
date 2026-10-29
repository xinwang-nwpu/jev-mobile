"""Exact four-field A11Y selectors and per-snapshot duplicate annotations."""

from collections import Counter
from copy import deepcopy


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


def find_matches(tree, selector):
    """Match all four fields exactly; missing fields are represented by None."""
    if set(selector) != set(SELECTOR_FIELDS):
        raise ValueError("Selector must contain exactly: " + ", ".join(SELECTOR_FIELDS))
    identity = _identity(selector)
    return [node for node in _walk(tree) if _identity(node) == identity]


def annotate_duplicates(tree):
    """Retain every node and its structure, without changing the live input tree."""
    saved_tree = deepcopy(tree)
    nodes = list(_walk(saved_tree))
    counts = Counter(_identity(node) for node in nodes)
    for node in nodes:
        node["match_count"] = counts[_identity(node)]
        node["is_duplicate"] = node["match_count"] > 1
    groups = [{"selector": dict(zip(SELECTOR_FIELDS, identity)), "match_count": count}
              for identity, count in counts.items() if count > 1]
    return {
        "selector_fields": list(SELECTOR_FIELDS),
        "node_count": len(nodes),
        "duplicate_group_count": len(groups),
        "duplicate_node_count": sum(group["match_count"] for group in groups),
        "duplicate_groups": groups,
        "tree": saved_tree,
    }
