"""Tests for the root ``untaped profile …`` command group.

The profile surface is plugin-agnostic: it operates on ``config.yml``'s
base (``default``, the top level) and its ``profiles:``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel, SecretStr

from untaped.runtime import bootstrap
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import profile_scope
from untaped.runtime.settings import FORMAT_VERSION, get_settings, register_profile_settings
from untaped.sdk.testing import CliInvoker, ScriptedPromptBackend, TtyStringIO, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _root() -> App:
    return bootstrap.build_root_app(candidates=[]).meta


_MANAGEMENT_VERBS = ("list", "show", "current", "create", "delete", "rename")


class _GithubSettings(BaseModel):
    token: SecretStr | None = None


@pytest.fixture
def app(_isolated_config: Path):
    register_profile_settings("github", _GithubSettings)
    get_settings.cache_clear()
    return _root()


def _seed(cfg: Path) -> None:
    cfg.write_text(
        "format_version: 2\n"
        "log_level: INFO\n"
        "profiles:\n  prod:\n    github:\n      token: ghp_secret\n"
        "  stage:\n    log_level: DEBUG\n",
        encoding="utf-8",
    )


def test_root_profile_exposes_all_verbs() -> None:
    app = _root()["profile"]
    for verb in _MANAGEMENT_VERBS:
        assert verb in app
    assert "use" not in app  # a run picks with --profile or UNTAPED_PROFILE


def test_a_fresh_home_lists_the_base() -> None:
    result = CliInvoker().invoke(_root(), ["profile", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"name": "default", "selected": True, "keys": 0}]


def test_list_ignores_the_format_version_stamp(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        f"format_version: {FORMAT_VERSION}\nprofiles:\n  work: {{}}\n", encoding="utf-8"
    )
    app = _root()
    result = CliInvoker().invoke(
        app, ["profile", "list", "--format", "raw", "--columns", "name", "--columns", "keys"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["default\t0", "work\t0"]


def test_create_and_current_round_trip(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _root()
    assert CliInvoker().invoke(app, ["profile", "create", "work"]).exit_code == 0
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    current = CliInvoker().invoke(app, ["profile", "current"])
    assert current.exit_code == 0, current.output
    assert current.stdout.strip() == "work"
    assert "(source: env)" in current.stderr


def test_current_names_the_base_when_nothing_picks() -> None:
    current = CliInvoker().invoke(_root(), ["profile", "current"])
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
    result = CliInvoker().invoke(app, ["profile", *args])
    assert result.exit_code == 1
    assert "base" in result.stderr
    assert read_config_dict(_isolated_config)["log_level"] == "INFO"


def test_show_default_is_the_base(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["profile", "show", "default", "--raw", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"log_level": "INFO"}


def test_create_copies_the_base(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    assert (
        CliInvoker().invoke(app, ["profile", "create", "qa", "--copy-from", "default"]).exit_code
        == 0
    )
    assert read_config_dict(_isolated_config)["profiles"]["qa"] == {"log_level": "INFO"}


def test_delete_accepts_dash_y_alias(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["profile", "delete", "stage", "-y"])
    assert result.exit_code == 0, result.output
    assert "deleted profile: stage" in result.stderr


def test_delete_without_confirmation_does_not_mutate_noninteractive_input(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    result = invoke_cli(app, ["profile", "delete", "stage"])
    assert result.exit_code == 2
    assert "requires --yes" in f"{result.stderr}{result.exception or ''}"
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


def test_delete_decline_is_cancelled_with_exit_one(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    backend = ScriptedPromptBackend(confirms=[False])
    # stdin is a pipe, so the prompt goes to the controlling terminal.
    result = invoke_cli(app, ["profile", "delete", "stage"], terminal=True, prompt_backend=backend)
    assert result.exit_code == 1
    assert backend.calls == [("confirm", "Delete profile 'stage'?")]
    assert result.stderr.endswith("\ncancelled; no changes made\n")
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


def test_rename_updates_profiles(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["profile", "rename", "stage", "staging"])
    assert result.exit_code == 0, result.output
    profiles = read_config_dict(_isolated_config)["profiles"]
    assert "staging" in profiles
    assert "stage" not in profiles


def test_show_redacts_secrets(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["profile", "show", "prod"])
    assert result.exit_code == 0, result.output
    assert "ghp_secret" not in result.stdout
    assert "***" in result.stdout


def test_current_honours_root_profile_flag(_isolated_config: Path) -> None:
    _seed(_isolated_config)
    app = _root()
    with profile_scope("stage"):
        result = CliInvoker().invoke(app, ["profile", "current"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "stage"
    assert "(source: flag)" in result.stderr


def test_current_rejects_root_profile_flag_naming_missing_profile(_isolated_config: Path) -> None:
    _seed(_isolated_config)
    app = _root()
    with profile_scope("typo"):
        result = CliInvoker().invoke(app, ["profile", "current"])
    assert result.exit_code == 2  # a pick naming no profile is a usage error
    assert "'typo' (from flag) is not defined" in result.stderr


def test_current_lists_a_stacked_pick_with_each_source(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    app = _root()
    with profile_scope("stage"):
        plain = CliInvoker().invoke(app, ["profile", "current"])
        structured = CliInvoker().invoke(app, ["profile", "current", "--format", "json"])
    assert plain.stdout == "prod,stage\n"
    assert "(source: env, flag)" in plain.stderr
    assert json.loads(structured.stdout) == {
        "name": "prod,stage",
        "profiles": [{"name": "prod", "source": "env"}, {"name": "stage", "source": "flag"}],
    }
    assert structured.stderr == ""


def test_show_without_a_name_shows_the_stacked_pick(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    app = _root()
    with profile_scope("stage"):
        shown = CliInvoker().invoke(app, ["profile", "show", "--format", "json"])
        raw = CliInvoker().invoke(app, ["profile", "show", "--raw"])
    assert shown.exit_code == 0, shown.output
    assert (json.loads(shown.stdout)["name"], json.loads(shown.stdout)["selected"]) == (
        "prod,stage",
        True,
    )
    assert raw.exit_code == 2
    assert "--raw shows one profile" in raw.stderr


def test_list_json_marks_the_selected_with_booleans(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    app = _root()
    result = CliInvoker().invoke(app, ["profile", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    selected = {row["name"]: row["selected"] for row in json.loads(result.stdout)}
    assert selected == {"default": False, "prod": True, "stage": False}
    with profile_scope("stage"):
        stacked = CliInvoker().invoke(app, ["profile", "list", "--format", "json"])
    selected = {row["name"]: row["selected"] for row in json.loads(stacked.stdout)}
    assert selected == {"default": False, "prod": True, "stage": True}


def test_list_marks_the_selected_with_a_check_in_tables_only(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    app = _root()
    raw = CliInvoker().invoke(
        app, ["profile", "list", "--format", "raw", "--columns", "name", "--columns", "selected"]
    )
    assert raw.stdout.splitlines() == ["default\tFalse", "prod\tTrue", "stage\tFalse"]
    table = CliInvoker().invoke(app, ["profile", "list"])
    marked = {line.split("│")[1].strip() for line in table.stdout.splitlines() if "✓" in line}
    assert marked == {"prod"}


# ── outcome records and --dry-run ────────────────────────────────────────────


def _outcome(app, args: list[str]) -> dict[str, object]:
    result = CliInvoker().invoke(app, ["profile", *args, "--format", "json"])
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

    again = CliInvoker().invoke(app, ["profile", "create", "qa", "--dry-run"])
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
    result = invoke_cli(app, ["profile", "delete", "stage", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "planned"
    assert "top-level keys: log_level" in result.stderr
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


def test_delete_dry_run_still_refuses_the_base(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["profile", "delete", "default", "--dry-run"])
    assert result.exit_code == 1
    assert "it is the base" in result.stderr


def test_delete_emits_a_deleted_outcome(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    assert _outcome(app, ["delete", "stage", "--yes"])["action"] == "deleted"
    assert "stage" not in read_config_dict(_isolated_config).get("profiles", {})


@pytest.mark.parametrize("name", ["", "my work", "a,b", ".hidden"])
def test_create_and_rename_refuse_a_name_profile_cannot_pick(
    app, _isolated_config: Path, name: str
) -> None:
    _seed(_isolated_config)
    created = CliInvoker().invoke(app, ["profile", "create", name])
    renamed = CliInvoker().invoke(app, ["profile", "rename", "stage", name])
    for result in (created, renamed):
        assert result.exit_code == 2, result.output  # a usage error, as in a pick
        assert f"invalid profile name {name!r}" in result.stderr
    assert set(read_config_dict(_isolated_config)["profiles"]) == {"prod", "stage"}


@pytest.mark.parametrize(
    ("pick", "args", "message"),
    [
        ("typo", ["create", "qa"], "profile 'typo' is not defined"),
        ("typo", ["rename", "stage", "qa"], "profile 'typo' is not defined"),
        ("typo", ["delete", "stage", "--yes"], "profile 'typo' is not defined"),
        ("stage", ["delete", "stage", "--yes"], "profile 'stage' is in this run's pick"),
        ("stage", ["rename", "stage", "qa"], "profile 'stage' is in this run's pick"),
    ],
)
def test_a_change_that_leaves_the_pick_undefined_writes_nothing(
    app,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    pick: str,
    args: list[str],
    message: str,
) -> None:
    _seed(_isolated_config)
    before = _isolated_config.read_text(encoding="utf-8")
    monkeypatch.setenv("UNTAPED_PROFILE", pick)

    result = CliInvoker().invoke(app, ["profile", *args])

    assert result.exit_code == 2, result.output
    assert message in result.stderr
    assert _isolated_config.read_text(encoding="utf-8") == before


def test_creating_the_profile_the_pick_misses_is_the_way_out(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "typo")

    assert _outcome(app, ["create", "typo"])["action"] == "created"
