"""Readable write previews and the ``jira.confirm`` policy (pure, no I/O).

A preview is one ``METHOD path`` line per request followed by indented
``field: old → new`` lines, instead of the raw JSON body. Old values come
from the caller (the issue as it is now) and are ``None`` when not fetched.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Mapping
from typing import Any, Literal

# ``jira.confirm``: which writes ask first.
ConfirmPolicy = Literal["always", "destructive", "never"]

_ARROW = "→"
_TEXT_LIMIT = 60
# Keys that name a Jira object (user, status, option, project, …), best first.
_NAME_KEYS = ("name", "key", "value", "displayName", "id")
# ``update`` operations that only add to a field; every other one replaces or removes.
_ADDITIVE_OPS = frozenset({"add"})
_OP_SIGNS = {"add": "+", "remove": "-"}


def needs_confirmation(policy: ConfirmPolicy, *, destructive: bool) -> bool:
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


def render_value(value: Any, *, key: str | None = None, limit: int | None = _TEXT_LIMIT) -> str:
    """One human-readable rendering of a Jira field value.

    A Jira object renders as its ``key`` when it has one (else its first
    naming key); text longer than ``limit`` is cut (``None`` keeps it whole).
    """
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if limit is not None and len(value) > limit:
            value = value[: limit - 1] + "…"
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        for name in (key, *_NAME_KEYS) if key else _NAME_KEYS:
            if value.get(name) is not None:
                return str(value[name])
        if all(item is None for item in value.values()):
            return "(none)"
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(render_value(item, key=key, limit=limit) for item in value) + "]"
    return str(value)


def change_line(name: str, new: Any, *, old: Any = None, compare: bool = True) -> str:
    """``name: old → new``, ``name: new (unchanged)``, or ``name: new`` without ``compare``.

    The old value is named the way the new one is (``{"id": "2"}`` shows the
    current priority's id, not its name), and whole values are compared even
    though long text is shown cut.
    """
    key = _naming_key(new)
    rendered = render_value(new, key=key)
    if not compare:
        return f"{name}: {rendered}"
    if render_value(old, key=key, limit=None) == render_value(new, key=key, limit=None):
        return f"{name}: {rendered} (unchanged)"
    return f"{name}: {render_value(old, key=key)} {_ARROW} {rendered}"


def comment_lines(body: str) -> list[str]:
    """A comment's preview: a ``comment:`` line, then the whole body indented."""
    return ["comment:", textwrap.indent(body, "  ")]


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
    """The field ids whose current value an edit's diff shows (set or ``set`` op), in order.

    Fields touched only by ``add``/``remove``/``edit`` operations are left
    out: their preview does not show the old value.
    """
    if not payload:
        return []
    names = list(payload.get("fields") or {})
    for name, operations in (payload.get("update") or {}).items():
        if any(isinstance(op, Mapping) and "set" in op for op in _as_list(operations)):
            names.append(name)
    return list(dict.fromkeys(names))


def transition_changes(
    transition: Mapping[str, Any],
    current: Mapping[str, Any] | None,
    *,
    available: bool = True,
    comment: str | None = None,
    resolution: str | None = None,
) -> list[str]:
    """Lines for a transition: its name, the status change, resolution and comment.

    ``current`` is the issue's fields, ``None`` when they could not be read;
    ``available=False`` says the transition is not offered from its status.
    """
    transition_id, name, target = transition.get("id"), transition.get("name"), transition.get("to")
    if not available:
        after = "(not available from this status)"
    else:
        after = render_value(target) if target else "(unknown)"
    before = "(unknown)" if current is None else render_value(current.get("status"))
    lines = [
        f"transition: {name} ({transition_id})" if name else f"transition: {transition_id}",
        f"status: {before} {_ARROW} {after}",
    ]
    if resolution is not None:
        new = {"name": resolution}
        if current is None:
            lines.append(f"resolution: (unknown) {_ARROW} {render_value(new)}")
        else:
            lines.append(change_line("resolution", new, old=current.get("resolution")))
    if comment is not None:
        lines.extend(comment_lines(comment))
    return lines


def _naming_key(value: Any) -> str | None:
    """The key that names a Jira object (or a list's first object), if any."""
    if isinstance(value, list | tuple):
        return next((k for item in value if (k := _naming_key(item))), None)
    if isinstance(value, Mapping):
        return next((k for k in _NAME_KEYS if value.get(k) is not None), None)
    return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]
