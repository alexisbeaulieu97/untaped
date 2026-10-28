"""A deprecated SDK callable carries both marks, and they agree.

untaped's ``@deprecated(removed_in=…)`` dates it on the surface; the
``warnings.deprecated`` literal under it is what type checkers report, and
must be the mark's sentence. Core's SDK modules are no plugin's, so this
test runs the check ``untaped lint`` runs on a plugin's ``api`` and ``testing``
(``lint.deprecation-text`` and its siblings) over them and every first-party
plugin's.
"""

from __future__ import annotations

import importlib

import pytest

from repo.support import FIRST_PARTY
from untaped.core.plugin_check.stability import deprecation_text_violations
from untaped.core.surface import SDK_MODULES, plugin_sdk_modules

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
