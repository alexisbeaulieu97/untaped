"""The ``ui.format`` setting and ``UNTAPED_FORMAT`` pick the default ``--format``.

Only commands taking the shared ``--format`` with the ``table`` default
follow them; an explicit ``--format`` always wins, and ``UNTAPED_FORMAT``
wins over the setting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from cyclopts import App

from test_management.support import GithubProfile, make_spec, write_config
from untaped import bootstrap
from untaped.sdk import CapabilitySpec, FormatOption, create_app, emit
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")

_CONFIG = "profiles:\n  default:\n    github:\n      base_url: https://g\n"


def _invoke(argv: list[str]) -> CliResult:
    root = bootstrap.build_root_app(
        builtins=(make_spec("github", profile_model=GithubProfile),), externals=()
    )
    return CliInvoker().invoke(root.meta, argv)


def _base_url_row(stdout: str) -> dict[str, object]:
    rows = json.loads(stdout)
    return next(row for row in rows if row["key"] == "github.base_url")


def test_ui_format_setting_changes_the_table_default(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG + "    ui:\n      format: json\n")
    result = _invoke(["config", "list"])
    assert result.exit_code == 0, result.output
    assert _base_url_row(result.stdout)["value"] == "https://g"


def test_untaped_format_wins_over_the_setting(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _CONFIG + "    ui:\n      format: json\n")
    monkeypatch.setenv("UNTAPED_FORMAT", "yaml")
    result = _invoke(["config", "list"])
    assert result.exit_code == 0, result.output
    rows = yaml.safe_load(result.stdout)
    assert {"key": "github.base_url", "value": "https://g"}.items() <= next(
        row for row in rows if row["key"] == "github.base_url"
    ).items()


@pytest.mark.parametrize("flag", ["--format", "-f"])
def test_explicit_format_wins(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    write_config(_isolated_config, _CONFIG + "    ui:\n      format: yaml\n")
    monkeypatch.setenv("UNTAPED_FORMAT", "yaml")
    result = _invoke(["config", "list", flag, "json"])
    assert result.exit_code == 0, result.output
    assert _base_url_row(result.stdout)["value"] == "https://g"


def test_commands_with_another_default_keep_it(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _CONFIG)
    monkeypatch.setenv("UNTAPED_FORMAT", "json")
    result = _invoke(["config", "get", "github.base_url"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "https://g"


def test_invalid_untaped_format_is_a_usage_error(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _CONFIG)
    monkeypatch.setenv("UNTAPED_FORMAT", "xml")
    result = _invoke(["config", "list"])
    assert result.exit_code == 2
    assert "error: UNTAPED_FORMAT" in result.stderr
    assert "'xml'" in result.stderr


@pytest.mark.parametrize("command", [["doctor"], ["setup"]])
def test_an_invalid_untaped_format_does_not_block_diagnosis(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    write_config(_isolated_config, _CONFIG)
    monkeypatch.setenv("UNTAPED_FORMAT", "xml")
    result = _invoke(command)
    assert "UNTAPED_FORMAT" not in result.stderr
    assert result.exit_code != 2 or "terminal" in result.stderr


def _lazy_spec() -> CapabilitySpec:
    def factory() -> App:
        app = create_app(name="lazy", help="Lazy capability.")

        @app.command(name="list")
        def list_command(*, fmt: FormatOption = "table") -> None:
            """List one row."""
            emit([{"name": "row"}], fmt=fmt)

        return app

    return CapabilitySpec(
        name="lazy",
        app_factory=factory,
        config_section="lazy",
        profile_model=GithubProfile,
        help="Lazy capability.",
    )


def test_ui_format_reaches_a_lazily_mounted_capability(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      format: json\n")
    root = bootstrap.build_root_app(builtins=(_lazy_spec(),), externals=())
    result = CliInvoker().invoke(root.meta, ["lazy", "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"name": "row"}]


def test_an_invalid_ui_section_falls_back_so_it_can_be_repaired(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      format: xml\n")
    result = _invoke(["config", "set", "ui.format", "json"])
    assert result.exit_code == 0, result.output
    assert "key" in result.stdout
    assert json.loads(_invoke(["config", "list"]).stdout)


def test_format_help_names_the_overrides_only_for_the_table_default() -> None:
    def help_text(argv: list[str]) -> str:
        return " ".join(_invoke([*argv, "--help"]).stdout.replace("│", " ").split())

    assert "[default: table, or UNTAPED_FORMAT / ui.format]" in help_text(["config", "list"])
    raw_help = help_text(["config", "get"])
    assert "[default: raw]" in raw_help
    assert "UNTAPED_FORMAT" not in raw_help
