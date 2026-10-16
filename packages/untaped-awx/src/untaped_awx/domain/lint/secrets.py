"""Secrets in launch variables: where they sit, which values are plaintext, and redacting them.

A launch variable is a template's ``extra_vars``, a schedule's ``extra_data``
or a workflow node's ``prompts.extra_vars``. Its secret-named keys are the
ones :func:`~untaped_awx.domain.secret_names.looks_secret` names, the list the
launch preview redacts with. Two readers share these: ``plaintext-secret``
reports such a key with a plaintext value in a document, and the controller
loader redacts them, and those of inventory variables, before the snapshot
is cached, so a live ``password`` never lands in a file nobody asked for.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from typing import Any

from untaped_awx.domain.envelope import Resource
from untaped_awx.domain.secret_names import looks_secret, redact_secret_names
from untaped_awx.domain.secret_paths import PLACEHOLDER
from untaped_awx.domain.structured_text import parse_structured_text

LAUNCH_VARIABLES: dict[str, str] = {
    "JobTemplate": "extra_vars",
    "WorkflowJobTemplate": "extra_vars",
    "Schedule": "extra_data",
}
"""Kind → the spec field holding its launch variables (a node's are ``prompts.extra_vars``)."""

_INVENTORY_VARIABLES: dict[str, tuple[str, ...]] = {
    "Inventory": ("variables", "source_vars"),
    "InventorySource": ("source_vars",),
    "Group": ("variables",),
    "Host": ("variables",),
}
"""Kind → its inventory variable fields, which often hold connection secrets."""


def is_plaintext(value: Any) -> bool:
    """Whether ``value`` is a secret written out: set, not ``$encrypted$``, not a template."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int | float):
        return True
    return isinstance(value, str) and value not in ("", PLACEHOLDER) and "{{" not in value


def launch_variables(resource: Resource) -> Iterator[tuple[str, Any]]:
    """``(field, variables)`` for each launch-variable field ``resource`` sets, text parsed."""
    spec = resource.spec
    field = LAUNCH_VARIABLES.get(resource.kind)
    if field is not None and field in spec:
        yield field, parse_structured_text(spec[field])
    for label, prompts in _node_prompts(resource):
        if "extra_vars" in prompts:
            yield f"{label}.prompts.extra_vars", parse_structured_text(prompts["extra_vars"])


def plaintext_secrets(variables: Any, *, path: str = "") -> Iterator[str]:
    """The dotted path of each secret-named key in ``variables`` holding a plaintext value."""
    if isinstance(variables, Mapping):
        for key, value in variables.items():
            where = f"{path}.{key}" if path else str(key)
            if looks_secret(str(key)) and is_plaintext(value):
                yield where
            else:
                yield from plaintext_secrets(value, path=where)
    elif isinstance(variables, list):
        for index, value in enumerate(variables):
            yield from plaintext_secrets(value, path=f"{path}[{index}]")


def redact_variables(resource: Resource) -> Resource:
    """``resource`` with every secret-named variable replaced by ``$encrypted$``.

    Its launch variables, and an inventory's, a group's or a host's
    variables (``ansible_password``). Text stays text (written back as JSON)
    and is rewritten only when a value changed, so a document without a
    secret keeps its exact text.
    """
    redacted = resource.model_copy(deep=True)
    spec = redacted.spec
    fields = (LAUNCH_VARIABLES.get(resource.kind), *_INVENTORY_VARIABLES.get(resource.kind, ()))
    for field in fields:
        if field is not None and field in spec:
            spec[field] = _redacted(spec[field])
    for _label, prompts in _node_prompts(redacted):
        if "extra_vars" in prompts:
            prompts["extra_vars"] = _redacted(prompts["extra_vars"])
    return redacted


def _redacted(value: Any) -> Any:
    parsed = parse_structured_text(value)
    if not isinstance(parsed, dict | list):
        return value
    hidden = redact_secret_names("", copy.deepcopy(parsed), replacement=PLACEHOLDER)
    if hidden == parsed:
        return value
    return json.dumps(hidden) if isinstance(value, str) else hidden


def _node_prompts(resource: Resource) -> Iterator[tuple[str, dict[str, Any]]]:
    """``(nodes[<id>], prompts)`` for each node of a workflow document that has prompts."""
    if resource.kind != "WorkflowJobTemplate":
        return
    nodes = resource.spec.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("prompts"), dict):
            yield f"nodes[{node.get('id')}]", node["prompts"]


__all__ = [
    "LAUNCH_VARIABLES",
    "is_plaintext",
    "launch_variables",
    "plaintext_secrets",
    "redact_variables",
]
