"""stderr messages follow ``untaped.ui.theme``, resolved once settings load (text only)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from core_test_support.commands.support import ExtProfile, make_plugin, write_config
from untaped.runtime import bootstrap
from untaped.sdk import Command, Diagnostics, Output
from untaped.sdk._run import current_run
from untaped.sdk.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_CONFIG = "format_version: 2\n"
#: What ``high-contrast`` makes of a warning: bold yellow.
_YELLOW = "\x1b[1;33mwarning: careful\x1b[0m\n"


class Said(BaseModel):
    """Nothing: the commands only warn."""

    name: str


class Warn(Command):
    """Print a warning."""

    output: Output[Said]

    def run(self) -> Iterator[Said]:
        Diagnostics().warning("careful")
        yield from ()


class Theme(Command):
    """Say whether a theme was resolved."""

    output: Output[Said]

    def run(self) -> Iterator[Said]:
        Diagnostics().warning(f"theme resolved: {current_run().policy.theme is not None}")
        yield from ()


def _invoke(argv: list[str]) -> CliResult:
    ext = make_plugin("ext", settings=ExtProfile, commands=(Warn, Theme))
    root = bootstrap.build_root_app(candidates=(plugin_candidate(ext),))
    return CliInvoker().invoke(root.meta, argv)


def test_a_warning_follows_ui_theme(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    write_config(_isolated_config, _CONFIG + "untaped:\n  ui:\n    theme: high-contrast\n")
    result = _invoke(["ext", "warn"])
    assert result.exit_code == 0, result.output
    assert result.stderr == _YELLOW


def test_an_unknown_theme_still_runs_with_the_default(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    write_config(_isolated_config, _CONFIG + "untaped:\n  ui:\n    theme: no-such-theme\n")
    result = _invoke(["ext", "warn"])
    assert result.exit_code == 0, result.output
    assert result.stderr.endswith("warning: careful\n")
    assert "\x1b[1;33m" not in result.stderr


def test_json_diagnostics_resolve_no_theme(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG + "untaped:\n  ui:\n    theme: high-contrast\n")
    assert _invoke(["ext", "theme"]).stderr == "warning: theme resolved: True\n"
    structured = _invoke(["ext", "theme", "-f", "json"])
    assert '"message": "theme resolved: False"' in structured.stderr
