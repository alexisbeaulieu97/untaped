"""End-to-end tests for ``untaped profile <…>`` via Typer's ``CliRunner``."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner
from untaped_core import get_settings
from untaped_profile import app


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


def _seed(cfg: Path) -> None:
    cfg.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    awx:\n      base_url: https://prod\n"
        "  stage:\n    awx:\n      base_url: https://stage\n"
        "active: prod\n"
    )


# ---- list ----


def test_list_outputs_all_profiles(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    names = result.stdout.splitlines()
    assert sorted(names) == ["default", "prod", "stage"]


def test_list_marks_active_profile(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(
        app,
        ["list", "--format", "raw", "--columns", "name", "--columns", "active"],
    )
    assert result.exit_code == 0
    rows = {line.split("\t")[0]: line.split("\t")[1] for line in result.stdout.splitlines()}
    assert rows["prod"] == "✓"
    assert rows["default"] == ""


# ---- show ----


def test_show_default_returns_resolved_view(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["show", "prod"])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.stdout)
    assert payload == {"log_level": "INFO", "awx": {"base_url": "https://prod"}}


def test_show_raw_returns_only_what_profile_sets(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["show", "prod", "--raw"])
    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    assert payload == {"awx": {"base_url": "https://prod"}}


def test_show_unknown_profile_errors(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["show", "ghost"])
    assert result.exit_code != 0


def test_show_json_emits_structured_envelope(_isolate_config: Path) -> None:
    """`untaped profile show prod --format json | jq '.data'` is the
    documented usage. JSON output wraps the profile data in
    ``{name, active, raw, data}`` so jq users can address each field."""
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["show", "prod", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["name"] == "prod"
    assert payload["active"] is True
    assert payload["raw"] is False
    assert payload["data"] == {"log_level": "INFO", "awx": {"base_url": "https://prod"}}


def test_show_json_raw_flag_is_recorded(_isolate_config: Path) -> None:
    """``raw=True`` shows up in the JSON envelope so a downstream
    consumer can tell which view was rendered."""
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["show", "prod", "--raw", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["raw"] is True
    assert payload["data"] == {"awx": {"base_url": "https://prod"}}


@pytest.mark.parametrize("bad_fmt", ["raw", "table"])
def test_show_rejects_unsupported_formats(_isolate_config: Path, bad_fmt: str) -> None:
    """`raw` and `table` make no sense for a single nested object — silently
    falling through to YAML would lie to scripts that requested a specific
    pipe-friendly shape. Reject at parse time instead."""
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["show", "prod", "--format", bad_fmt])
    assert result.exit_code != 0
    assert bad_fmt in result.output.lower() or "invalid" in result.output.lower()


# ---- use ----


def test_use_persists_active(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["use", "stage"])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(_isolate_config.read_text())["active"] == "stage"


def test_use_unknown_profile_errors(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["use", "ghost"])
    assert result.exit_code != 0


# ---- create ----


def test_create_empty_profile(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["create", "homelab"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_config.read_text())
    assert data["profiles"]["homelab"] == {}


def test_create_with_copy_from(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["create", "homelab", "--copy-from", "prod"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_config.read_text())
    assert data["profiles"]["homelab"] == {"awx": {"base_url": "https://prod"}}


def test_create_rejects_existing(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["create", "prod"])
    assert result.exit_code != 0


# ---- delete ----


def test_delete_removes_profile(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["delete", "stage"])
    assert result.exit_code == 0, result.output
    assert "stage" not in yaml.safe_load(_isolate_config.read_text())["profiles"]


def test_delete_default_refused(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["delete", "default"])
    assert result.exit_code != 0


def test_delete_active_refused(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["delete", "prod"])
    assert result.exit_code != 0


# ---- rename ----


def test_rename_keeps_data_and_updates_active(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["rename", "prod", "production"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_config.read_text())
    assert data["profiles"]["production"] == {"awx": {"base_url": "https://prod"}}
    assert "prod" not in data["profiles"]
    assert data["active"] == "production"


def test_rename_default_refused(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["rename", "default", "main"])
    assert result.exit_code != 0


def test_rename_collision_refused(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    result = CliRunner().invoke(app, ["rename", "stage", "prod"])
    assert result.exit_code != 0


# ---- help / no-args ----


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("list", "show", "use", "create", "delete", "rename"):
        assert cmd in result.stdout


def test_no_args_shows_help() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 2
    assert "Manage configuration profiles" in result.output or "Manage" in result.output
