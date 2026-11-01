"""``docs/reference/conventions.md``'s SDK stability names exactly the modules a plugin may import.

The set is core's SDK modules and core's ``api`` module, as lint's
``import-boundary`` rule allows them.
"""

from __future__ import annotations

import re

from repo.support import REPO_ROOT
from untaped.core.surface import SDK_MODULES
from untaped_lint.providers.conventions.imports import CORE_API


def test_sdk_stability_lists_the_modules_a_plugin_imports() -> None:
    text = (REPO_ROOT / "docs" / "reference" / "conventions.md").read_text(encoding="utf-8")
    section = text.split("\n## SDK stability\n", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"`(untaped\.[a-z_.]+)`", section))
    assert listed == {*SDK_MODULES, CORE_API}
