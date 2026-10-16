"""``awx.plaintext-secret``: a secret written out in a document.

Two places hold secrets: the kind's secret paths (a survey's password
defaults, a host config key) and its launch variables, by the names the
launch preview redacts (:mod:`~untaped_awx.domain.lint.secrets`). A value
that is ``$encrypted$`` (keep the controller's), empty, or a Jinja
expression is not plaintext. Live resources come redacted, so it fires on
documents only.
"""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import AwxResources
from untaped_awx.domain.lint.rules._support import report
from untaped_awx.domain.lint.secrets import is_plaintext, launch_variables, plaintext_secrets
from untaped_awx.domain.secret_paths import values_at
from untaped_lint.api import Finding, rule

_HINT = "keep it in a credential or a vault, or write $encrypted$ to keep the controller's value"


@experimental
@rule(
    "plaintext-secret",
    reads=AwxResources,
    severity="error",
    summary="A document holds no secret in plain text.",
)
def plaintext_secret(resources: AwxResources) -> Iterator[Finding]:
    for item in resources.items:
        facts = resources.kinds.get(item.resource.kind)
        if item.origin != "document":
            continue
        for pattern in facts.secret_paths if facts is not None else ():
            if any(is_plaintext(value) for value in values_at(item.resource.spec, pattern)):
                yield report(
                    plaintext_secret, item, f"{pattern} holds a secret in plain text", hint=_HINT
                )
        for field, variables in launch_variables(item.resource):
            for path in plaintext_secrets(variables):
                yield report(
                    plaintext_secret,
                    item,
                    f"{field}: {path} looks like a secret in plain text",
                    hint=_HINT,
                )
