"""``awx.plaintext-secret``: a secret written out in a document.

Two places hold secrets: the kind's secret paths (a survey's password
defaults, a host config key, a credential's ``inputs``) and its variables,
by the names the launch preview redacts (:mod:`~untaped_awx.domain.lint.secrets`).
A path ending in a wildcard (``inputs.*``) holds secrets only under those
names: a credential's ``username`` is not one. A value that is
``$encrypted$`` (keep the controller's), empty, or a Jinja expression is not
plaintext. Live resources come redacted, so it fires on documents only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.domain.lint.rules._support import report
from untaped_awx.domain.lint.secrets import is_plaintext, plaintext_secrets, variables_of
from untaped_awx.domain.secret_names import looks_secret
from untaped_awx.domain.secret_paths import path_slots
from untaped_lint.api import Finding, rule

_HINT = "keep it in a credential or a vault, or write $encrypted$ to keep the controller's value"


@experimental
@rule(
    "plaintext-secret",
    reads=Resources,
    severity="error",
    summary="A document holds no secret in plain text.",
)
def plaintext_secret(resources: Resources) -> Iterator[Finding]:
    for item in resources.items:
        facts = resources.kinds.get(item.resource.kind)
        if item.origin != "document":
            continue
        for pattern in facts.secret_paths if facts is not None else ():
            for path in _plaintext_at(item.resource.spec, pattern):
                yield report(
                    plaintext_secret, item, f"{path} holds a secret in plain text", hint=_HINT
                )
        for field, variables in variables_of(item.resource):
            for path in plaintext_secrets(variables):
                yield report(
                    plaintext_secret,
                    item,
                    f"{field}: {path} looks like a secret in plain text",
                    hint=_HINT,
                )


def _plaintext_at(spec: Any, pattern: str) -> Iterator[str]:
    """Where ``pattern`` holds a plaintext secret: the pattern once, or each secret-named key.

    Under a wildcard leaf only the keys :func:`looks_secret` names count, each
    reported by its own path (``inputs.password``).
    """
    head, _, leaf = pattern.rpartition(".")
    keyed = leaf == "*" or leaf.startswith("*[")
    found = False
    for container, key in path_slots(spec, pattern):
        if not is_plaintext(container[key]):
            continue
        if not keyed:
            found = True
        elif isinstance(container, Mapping) and looks_secret(str(key)):
            yield f"{head}.{key}" if head else str(key)
    if found:
        yield pattern
