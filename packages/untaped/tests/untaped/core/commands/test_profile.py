"""Tests for the root ``untaped profile …`` command group.

The profile surface is plugin-agnostic: it operates on ``config.yml``'s
base (``default``, the top level) and its ``profiles:``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.core.commands.profile import build_root_profile_app
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import profile_scope
from untaped.runtime.settings import FORMAT_VERSION, get_settings, register_profile_settings
from untaped.sdk.testing import CliInvoker, ScriptedPromptBackend, TtyStringIO, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")

_MANAGEMENT_VERBS = ("list", "show", "current", "create", "delete", "rename")


class _GithubSettings(BaseModel):
    token: SecretStr | None = None


@pytest.fixture
def app(_isolated_config: Path):
    register_profile_settings("github", _GithubSettings)
    get_settings.cache_clear()
    return build_root_profile_app()


def _seed(cfg: Path) -> None:
    cfg.write_text(
        "format_version: 2\n"
        "log_level: INFO\n"
        "profiles:\n  prod:\n    github:\n      token: ghp_secret\n"
        "  stage:\n    log_level: DEBUG\n",
        encoding="utf-8",
    )


def test_root_profile_exposes_all_verbs() -> None:
    app = build_root_profile_app()
    for verb in _MANAGEMENT_VERBS:
        assert verb in app
    assert "use" not in app  # a run picks with --profile or UNTAPED_PROFILE


def test_a_fresh_home_lists_the_base() -> None:
    result = CliInvoker().invoke(build_root_profile_app(), ["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"name": "default", "active": True, "keys": 0}]


def test_list_ignores_the_format_version_stamp(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        f"format_version: {FORMAT_VERSION}\nprofiles:\n  work: {{}}\n", encoding="utf-8"
    )
    app = build_root_profile_app()
    result = CliInvoker().invoke(
        app, ["list", "--format", "raw", "--columns", "name", "--columns", "keys"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["default\t0", "work\t0"]


def test_create_and_current_round_trip(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_root_profile_app()
    assert CliInvoker().invoke(app, ["create", "work"]).exit_code == 0
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    current = CliInvoker().invoke(app, ["current"])
    assert current.exit_code == 0, current.output
    assert current.stdout.strip() == "work"
    assert "(source: env)" in current.stderr


def test_current_names_the_base_when_nothing_picks() -> None:
    current = CliInvoker().invoke(build_root_profile_app(), ["current"])
    assert current.exit_code == 0, current.output
    assert current.stdout.strip() == "default"
    assert "(source: fallback)" in current.stderr


@pytest.mark.parametrize(
    "args", [["create", "default"], ["delete", "default", "--yes"], ["rename", "stage", "default"]]
)
def test_the_base_is_not_a_profile_to_create_delete_or_rename_to(
    app, _isolated_config: Path, args: list[str]
) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, args)
    assert result.exit_code == 1
    assert "base" in result.stderr
    assert read_config_dict(_isolated_config)["log_level"] == "INFO"


def test_show_default_is_the_base(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["show", "default", "--raw", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"log_level": "INFO"}


def test_create_copies_the_base(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    assert CliInvoker().invoke(app, ["create", "qa", "--copy-from", "default"]).exit_code == 0
    assert read_config_dict(_isolated_config)["profiles"]["qa"] == {"log_level": "INFO"}


def test_delete_accepts_dash_y_alias(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "stage", "-y"])
    assert result.exit_code == 0, result.output
    assert "deleted profile: stage" in result.stderr


def test_delete_without_confirmation_does_not_mutate_noninteractive_input(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    result = invoke_cli(app, ["delete", "stage"])
    assert result.exit_code == 2
    assert "requires --yes" in f"{result.stderr}{result.exception or ''}"
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


def test_delete_decline_is_cancelled_with_exit_one(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    backend = ScriptedPromptBackend(confirms=[False])
    # stdin is a pipe, so the prompt goes to the controlling terminal.
    result = invoke_cli(app, ["delete", "stage"], terminal=True, prompt_backend=backend)
    assert result.exit_code == 1
    assert backend.calls == [("confirm", "Delete profile 'stage'?")]
    assert result.stderr.endswith("\ncancelled; no changes made\n")
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


def test_rename_updates_profiles(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["rename", "stage", "staging"])
    assert result.exit_code == 0, result.output
    profiles = read_config_dict(_isolated_config)["profiles"]
    assert "staging" in profiles
    assert "stage" not in profiles


def test_show_redacts_secrets(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["show", "prod"])
    assert result.exit_code == 0, result.output
    assert "ghp_secret" not in result.stdout
    assert "***" in result.stdout


def test_current_honours_root_profile_flag(_isolated_config: Path) -> None:
    _seed(_isolated_config)
    app = build_root_profile_app()
    with profile_scope("stage"):
        result = CliInvoker().invoke(app, ["current"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "stage"
    assert "(source: flag)" in result.stderr


def test_current_rejects_root_profile_flag_naming_missing_profile(_isolated_config: Path) -> None:
    _seed(_isolated_config)
    app = build_root_profile_app()
    with profile_scope("typo"):
        result = CliInvoker().invoke(app, ["current"])
    assert result.exit_code == 4  # the active profile is not defined: config
    assert "'typo' (from flag) is not defined" in result.stderr


def test_list_json_marks_active_with_booleans(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    app = build_root_profile_app()
    result = CliInvoker().invoke(app, ["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    active = {row["name"]: row["active"] for row in json.loads(result.stdout)}
    assert active == {"default": False, "prod": True, "stage": False}


def test_list_marks_active_with_a_check_in_tables_only(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    app = build_root_profile_app()
    raw = CliInvoker().invoke(
        app, ["list", "--format", "raw", "--columns", "name", "--columns", "active"]
    )
    assert raw.stdout.splitlines() == ["default\tFalse", "prod\tTrue", "stage\tFalse"]
    table = CliInvoker().invoke(app, ["list"])
    marked = {line.split("│")[1].strip() for line in table.stdout.splitlines() if "✓" in line}
    assert marked == {"prod"}


# ── outcome records and --dry-run ────────────────────────────────────────────


def _outcome(app, args: list[str]) -> dict[str, object]:
    result = CliInvoker().invoke(app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_create_emits_outcome_and_dry_run_writes_nothing(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    planned = _outcome(app, ["create", "qa", "--copy-from", "stage", "--dry-run"])
    assert planned == {
        "name": "qa",
        "previous_name": None,
        "copied_from": "stage",
        "action": "planned",
    }
    assert "qa" not in read_config_dict(_isolated_config).get("profiles", {})
    assert _outcome(app, ["create", "qa"])["action"] == "created"
    assert "qa" in read_config_dict(_isolated_config)["profiles"]

    again = CliInvoker().invoke(app, ["create", "qa", "--dry-run"])
    assert again.exit_code == 1
    assert "already exists" in again.stderr


def test_rename_emits_outcome_and_dry_run_writes_nothing(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    planned = _outcome(app, ["rename", "stage", "staging", "--dry-run"])
    assert planned["action"] == "planned"
    assert "stage" in read_config_dict(_isolated_config)["profiles"]
    renamed = _outcome(app, ["rename", "stage", "staging"])
    assert (renamed["name"], renamed["previous_name"], renamed["action"]) == (
        "staging",
        "stage",
        "renamed",
    )


def test_delete_dry_run_previews_without_confirming_or_deleting(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    result = invoke_cli(app, ["delete", "stage", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "planned"
    assert "top-level keys: log_level" in result.stderr
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


def test_delete_dry_run_still_refuses_the_base(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "default", "--dry-run"])
    assert result.exit_code == 1
    assert "it is the base" in result.stderr


def test_delete_emits_a_deleted_outcome(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    assert _outcome(app, ["delete", "stage", "--yes"])["action"] == "deleted"
    assert "stage" not in read_config_dict(_isolated_config).get("profiles", {})
