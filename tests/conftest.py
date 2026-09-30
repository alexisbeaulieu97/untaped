"""Suite-wide hermetic baseline shared by every capability's tests.

Tests must not see the developer machine: the real ``~/.untaped`` config,
ambient ``UNTAPED_*`` overrides, git's ``GIT_CONFIG_*`` injection, or the
terminal width all used to change results locally while CI masked them with
workflow-level environment variables. Capability conftests may layer more
specific fixtures on top of this one.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, TextIO

import pytest
from pydantic import BaseModel

from untaped import cli
from untaped.auth import clear_token_cache
from untaped.prompts import reset_terminal_override, set_terminal_override
from untaped.records import table_columns_of
from untaped.settings import get_settings

_TERMINAL_ENV = {"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"}
# Ambient token fallbacks (``GH_TOKEN``) would otherwise leak a real token in.
_AMBIENT_ENV = frozenset(
    {
        "GIT_CONFIG",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "JIRA_API_TOKEN",
        "CONTROLLER_OAUTH_TOKEN",
        "TOWER_OAUTH_TOKEN",
        "AAP_TOKEN",
    }
)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TABLE_DEFAULTS = Path(__file__).parent / "conventions" / "baselines" / "table_defaults"


@pytest.fixture(autouse=True)
def _no_writes_to_the_repo_root() -> Iterator[None]:
    """Fail the test that leaves a new file at the repository root.

    A subprocess that inherits the process cwd (the repo root under pytest)
    once committed junk such as ``core.sshCommand/HEAD``; write into
    ``tmp_path`` or ``monkeypatch.chdir`` there instead. Coverage data files
    are exempt: parallel workers write them there while other tests run.
    """
    before = set(os.listdir(_REPO_ROOT))
    yield
    leaked = sorted(
        name for name in set(os.listdir(_REPO_ROOT)) - before if not name.startswith(".coverage")
    )
    if leaked:
        pytest.fail(f"test left files at the repository root: {', '.join(leaked)}")


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Pin a machine-independent process environment for each test."""
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        uv_cache = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")
        for key in list(os.environ):
            if key.startswith(("UNTAPED_", "GIT_CONFIG_")) or key in _AMBIENT_ENV:
                patch.delenv(key)
        patch.setenv("HOME", str(home))
        patch.setenv("UV_CACHE_DIR", uv_cache)
        # A fresh HOME hides uv's managed interpreters. Use the test runner's
        # Python for real hook workers instead of downloading it for each test.
        patch.setenv("UV_PYTHON", sys.executable)
        patch.setenv("UV_PYTHON_DOWNLOADS", "never")
        patch.setenv("UNTAPED_CONFIG", str(home / ".untaped" / "config.yml"))
        # ``UNTAPED_STATE`` was cleared above, so state.yml resolves next to
        # whichever temp config a test points ``UNTAPED_CONFIG`` at.
        assert "UNTAPED_STATE" not in os.environ
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for key, value in _TERMINAL_ENV.items():
            patch.setenv(key, value)
        get_settings.cache_clear()
        clear_token_cache()
        # No test may prompt on the developer's real terminal: the controlling
        # terminal is absent unless a test installs one (``invoke_cli(terminal=True)``).
        terminal_token = set_terminal_override(_no_controlling_terminal)
        try:
            yield
        finally:
            reset_terminal_override(terminal_token)
    get_settings.cache_clear()


def _no_controlling_terminal() -> TextIO:
    raise OSError("no controlling terminal in tests")


@pytest.fixture(autouse=True)
def table_default_violations(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail the test whose command emits a wide record collection without default columns.

    Records with more than four fields (``error`` aside) need default table
    columns: their type's ``table_columns`` or the command's ``table_columns=``
    (``docs/conventions.md``). Types listed under
    ``tests/conventions/baselines/table_defaults/`` are known violations;
    ``tests/conventions/test_table_defaults.py`` keeps that list shrinking.
    """
    found: list[str] = []
    emit_with = cli.emit_with

    def checked(records: Any, **kwargs: Any) -> None:
        if not isinstance(records, BaseModel | Mapping) and not kwargs.get("table_columns"):
            found.extend(_lacking_default_columns(records))
        emit_with(records, **kwargs)

    monkeypatch.setattr(cli, "emit_with", checked)
    yield found
    new = sorted(set(found) - _known_table_default_violations())
    if new:
        pytest.fail(
            "record collections emitted without default table columns (declare "
            "`table_columns` on the record; see docs/conventions.md#output-records):\n"
            + "\n".join(f"  {line}" for line in new)
        )


def _lacking_default_columns(records: Sequence[object]) -> Iterator[str]:
    for model in dict.fromkeys(type(item) for item in records if isinstance(item, BaseModel)):
        fields = {*model.model_fields, *model.model_computed_fields} - {"error"}
        own = model.__module__.startswith("untaped.")
        if own and len(fields) > 4 and not table_columns_of(model):
            yield f"{model.__module__}.{model.__qualname__}::no-default-columns"


@cache
def _known_table_default_violations() -> frozenset[str]:
    return frozenset(
        line
        for path in _TABLE_DEFAULTS.glob("*.txt")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )
