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
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo.support import FIRST_PARTY, REPO_ROOT
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


_CALLED = """\
import warnings

from untaped.sdk import UntapedDeprecationWarning


@warnings.deprecated("old is deprecated", category=UntapedDeprecationWarning)
def old() -> None: ...


old()
"""


def test_a_type_checker_still_reports_a_call_whose_literal_names_the_category(
    tmp_path: Path,
) -> None:
    """mypy, strict as this repo runs it, reports the literal whatever category it warns with."""
    plugin = tmp_path / "plugin.py"
    plugin.write_text(_CALLED)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(plugin),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "MYPYPATH": str(tmp_path)},
    )
    assert "old is deprecated  [deprecated]" in proc.stdout, proc.stdout + proc.stderr
