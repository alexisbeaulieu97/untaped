"""Readable write previews and the ``jira.confirm`` policy (pure, no I/O).

A preview is one ``METHOD path`` line per request followed by indented
``field: old → new`` lines, instead of the raw JSON body. Old values come
from the caller (the issue as it is now) and are ``None`` when not fetched.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_ARROW = "→"
_TEXT_LIMIT = 60
# Keys that name a Jira object (user, status, option, project, …), best first.
_NAME_KEYS = ("name", "key", "value", "displayName", "id")
# ``update`` operations that only add to a field; every other one replaces or removes.
_ADDITIVE_OPS = frozenset({"add"})
_OP_SIGNS = {"add": "+", "remove": "-"}


def needs_confirmation(policy: str, *, destructive: bool) -> bool:
    """Whether a write asks first under ``jira.confirm`` (``--yes`` still skips the prompt)."""
    return policy == "always" or (policy == "destructive" and destructive)


def is_destructive_patch(payload: Mapping[str, Any] | None, *, assigns: bool) -> bool:
    """Whether an issue edit can overwrite or remove what the issue holds now.

    Setting a field (``fields``) replaces its value, and an assignee change
    replaces the assignee; an ``update`` operation other than ``add``
    (``set``, ``remove``, ``edit``) replaces or removes. An edit that only
    adds (``update: {labels: [{add: x}]}``) is not destructive.
    """
    if assigns:
        return True
    if not payload:
        return False
    if payload.get("fields"):
        return True
    return any(
        op not in _ADDITIVE_OPS
        for operations in (payload.get("update") or {}).values()
        for operation in _as_list(operations)
        if isinstance(operation, Mapping)
        for op in operation
    )


def render_value(value: Any) -> str:
    """One short, human-readable rendering of a Jira field value."""
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        text = value if len(value) <= _TEXT_LIMIT else value[: _TEXT_LIMIT - 1] + "…"
        return json.dumps(text, ensure_ascii=False)
    if isinstance(value, Mapping):
        for key in _NAME_KEYS:
            if value.get(key) is not None:
                return str(value[key])
        if all(item is None for item in value.values()):
            return "(none)"
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(render_value(item) for item in value) + "]"
    return str(value)


def change_line(name: str, new: Any, *, old: Any = None, compare: bool = True) -> str:
    """``name: old → new``, ``name: new (unchanged)``, or ``name: new`` without ``compare``."""
    rendered = render_value(new)
    if not compare:
        return f"{name}: {rendered}"
    before = render_value(old)
    if before == rendered:
        return f"{name}: {rendered} (unchanged)"
    return f"{name}: {before} {_ARROW} {rendered}"


def payload_changes(
    payload: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> list[str]:
    """Lines for an issue create or edit body; ``current`` fields make them a diff."""
    compare = current is not None
    now = current or {}
    lines = [
        change_line(name, value, old=now.get(name), compare=compare)
        for name, value in (payload.get("fields") or {}).items()
    ]
    for name, operations in (payload.get("update") or {}).items():
        for operation in _as_list(operations):
            if not isinstance(operation, Mapping):
                continue
            for op, value in operation.items():
                if op == "set":
                    lines.append(change_line(name, value, old=now.get(name), compare=compare))
                else:
                    sign = _OP_SIGNS.get(op, op)
                    lines.append(f"{name}: {sign} {render_value(value)}")
    return lines


def referenced_fields(payload: Mapping[str, Any] | None) -> list[str]:
    """The field ids an edit body touches, in order (what a diff must read)."""
    if not payload:
        return []
    names = [*(payload.get("fields") or {}), *(payload.get("update") or {})]
    return list(dict.fromkeys(names))


def transition_changes(
    transition: Mapping[str, Any] | None,
    transition_id: str,
    current: Mapping[str, Any],
    *,
    comment: str | None = None,
    resolution: str | None = None,
) -> list[str]:
    """Lines for a transition: its name, the status change, resolution and comment."""
    name = (transition or {}).get("name")
    target = (transition or {}).get("to")
    lines = [
        f"transition: {name} ({transition_id})" if name else f"transition: {transition_id}",
        f"status: {render_value(current.get('status'))} {_ARROW} "
        + (render_value(target) if target else "(unknown)"),
    ]
    if resolution is not None:
        lines.append(change_line("resolution", {"name": resolution}, old=current.get("resolution")))
    if comment is not None:
        lines.append(f"comment: + {render_value(comment)}")
    return lines


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]
