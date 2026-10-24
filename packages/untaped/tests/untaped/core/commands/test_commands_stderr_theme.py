"""stderr messages follow ``untaped.ui.theme``, resolved once settings load (text only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cyclopts import App

from core_test_support.commands.support import ExtProfile, write_config
from untaped.runtime import bootstrap
from untaped.runtime.output_policy import current_policy
from untaped.runtime.registry import PluginSpec
from untaped.sdk import Diagnostics, FormatOption, create_app
from untaped.sdk.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_CONFIG = "profiles:\n  default:\n"
#: What ``high-contrast`` makes of a warning: bold yellow.
_YELLOW = "\x1b[1;33mwarning: careful\x1b[0m\n"


def _factory() -> App:
    app = create_app(name="ext", help="ext plugin.")

    @app.command
    def warn(*, format: FormatOption = "table") -> None:
        """Print a warning."""
        del format
        Diagnostics().warning("careful")

    @app.command
    def theme(*, format: FormatOption = "table") -> None:
        """Say whether a theme was resolved."""
        del format
        Diagnostics().warning(f"theme resolved: {current_policy().theme is not None}")

    return app


def _invoke(argv: list[str]) -> CliResult:
    spec = PluginSpec(name="ext", app_factory=_factory, settings=ExtProfile)
    root = bootstrap.build_root_app(candidates=(plugin_candidate(spec),))
    return CliInvoker().invoke(root.meta, argv)


def test_a_warning_follows_ui_theme(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    write_config(
        _isolated_config, _CONFIG + "    untaped:\n      ui:\n        theme: high-contrast\n"
    )
    result = _invoke(["ext", "warn"])
    assert result.exit_code == 0, result.output
    assert result.stderr == _YELLOW


def test_an_unknown_theme_still_runs_with_the_default(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    write_config(
        _isolated_config, _CONFIG + "    untaped:\n      ui:\n        theme: no-such-theme\n"
    )
    result = _invoke(["ext", "warn"])
    assert result.exit_code == 0, result.output
    assert result.stderr.endswith("warning: careful\n")
    assert "\x1b[1;33m" not in result.stderr


def test_json_diagnostics_resolve_no_theme(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, _CONFIG + "    untaped:\n      ui:\n        theme: high-contrast\n"
    )
    assert _invoke(["ext", "theme"]).stderr == "warning: theme resolved: True\n"
    structured = _invoke(["ext", "theme", "-f", "json"])
    assert '"message": "theme resolved: False"' in structured.stderr
