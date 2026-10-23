"""A deprecated SDK callable carries both marks, and they agree.

untaped's ``@deprecated(removed_in=…)`` dates it on the surface; the
``warnings.deprecated`` literal under it is what type checkers report, and
must be the mark's sentence. A parameter marked in its annotation warns
through ``@deprecated_parameters``. Core's SDK modules are no plugin's, so this
test runs the check ``untaped lint`` runs on a plugin's ``api`` and ``testing``
(``lint.deprecation-text`` and its siblings) over them and every first-party
plugin's.
"""

from __future__ import annotations

import importlib

import pytest

from repo.support import FIRST_PARTY, REPO_ROOT, SURFACE_FILE
from untaped.core.plugin_check.stability import deprecation_text_violations
from untaped.core.surface import SDK_MODULES, DeprecatedMark, parse, plugin_sdk_modules

SURFACE = REPO_ROOT / SURFACE_FILE

MODULES = sorted(
    {
        *SDK_MODULES,
        *(path for name in FIRST_PARTY for path in plugin_sdk_modules(f"untaped_{name}")),
    }
)


@pytest.mark.parametrize("path", MODULES)
def test_deprecated_sdk_callables_are_marked_twice_alike(path: str) -> None:
    found = deprecation_text_violations(importlib.import_module(path))
    assert not found, "\n".join(found)


def test_a_marked_parameter_is_dated_on_its_own_line() -> None:
    lines = {line.identity: line for line in parse(SURFACE.read_text(encoding="utf-8"))}
    assert lines["sdk untaped.sdk.echo(err=) default=false"].mark == DeprecatedMark(12, "ctx.diag")
