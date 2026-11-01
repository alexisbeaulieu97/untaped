"""A project's settings: ``config --project``, sources, profile renames and ``project init``'s pick.

The layer itself (order, refusals, trust) is tested in
``tests/untaped/runtime/test_folder_layer.py``; these drive the commands.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from core_test_support.commands.support import GithubProfile, make_plugin, plugin_candidate
from untaped.runtime import bootstrap
from untaped.runtime.folder_layer import forget_folder_layer
from untaped.runtime.settings import get_settings, load_project_config
from untaped.sdk.testing import CliResult, invoke_cli, make_project

pytestmark = pytest.mark.usefixtures("_isolated_config", "home_run")

HOME = """\
format_version: 2
github: {base_url: https://github.example.com}
profiles:
  staging: {untaped: {http: {timeout_seconds: 45}}}
  prod: {}
"""


@pytest.fixture(autouse=True)
def _home(_isolated_config: Path) -> Iterator[None]:
    _isolated_config.write_text(HOME, encoding="utf-8")
    get_settings.cache_clear()
    forget_folder_layer()
    yield
    forget_folder_layer()


def _root() -> App:
    github = make_plugin("github", settings=GithubProfile)
    return bootstrap.build_root_app(candidates=[plugin_candidate(github)]).meta


def _cli(*args: str) -> CliResult:
    return invoke_cli(_root(), list(args))


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str = "") -> Path:
    root = make_project(tmp_path / "PROJ-1").root
    if text:
        file = root / ".untaped" / "config.yml"
        file.write_text(text, encoding="utf-8")
        file.chmod(0o600)
    monkeypatch.chdir(root)
    return root


def _file(root: Path) -> dict[str, object]:
    return load_project_config(root / ".untaped" / "config.yml")


def test_config_set_and_unset_project_write_the_projects_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_config: Path
) -> None:
    root = _project(tmp_path, monkeypatch)
    home_before = _isolated_config.read_text(encoding="utf-8")
    result = _cli("config", "set", "untaped.http.timeout_seconds", "90", "--project")
    assert result.exit_code == 0, result.output
    assert "set untaped.http.timeout_seconds in project PROJ-1" in result.stderr
    assert _file(root)["untaped"] == {"http": {"timeout_seconds": 90.0}}
    assert _isolated_config.read_text(encoding="utf-8") == home_before
    got = _cli("config", "get", "untaped.http.timeout_seconds", "-f", "json")
    assert json.loads(got.stdout)["source"] == "project:PROJ-1"
    removed = _cli("config", "unset", "untaped.http.timeout_seconds", "--project")
    assert removed.exit_code == 0, removed.output
    assert "untaped" not in _file(root)


def test_config_set_project_refuses_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch)
    result = _cli("config", "set", "github.token", "ghp_x", "--project")
    assert result.exit_code == 4
    assert "github.token can't be set in" in result.stderr
    assert not (root / ".untaped" / "config.yml").exists()


def test_config_set_project_and_profile_are_one_place_too_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch)
    result = _cli("--profile", "prod", "config", "set", "untaped.ui.theme", "x", "--project")
    assert result.exit_code == 2
    assert "pick one" in result.stderr


def test_config_set_project_outside_a_project_is_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _cli("config", "set", "untaped.http.timeout_seconds", "9", "--project")
    assert result.exit_code == 2
    assert "not in a project" in result.stderr


def test_a_base_write_the_project_hides_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "untaped: {http: {timeout_seconds: 12}}\n")
    result = _cli("config", "set", "untaped.http.timeout_seconds", "60")
    assert result.exit_code == 0, result.output
    assert (
        "this run still reads untaped.http.timeout_seconds from project PROJ-1's config.yml "
        "over the base"
    ) in result.stderr


def test_config_list_names_each_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, "profile: staging\ngithub: {mode: 'on'}\n")
    result = _cli("config", "list", "-f", "json")
    rows = {row["key"]: row["source"] for row in json.loads(result.stdout)}
    assert rows["github.mode"] == "project:PROJ-1"
    assert rows["untaped.http.timeout_seconds"] == "profile:staging"
    assert rows["github.base_url"] == "base"


def test_profile_current_lists_the_projects_pick_and_what_it_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "profile: [ghost, staging]\n")
    result = _cli("--profile", "prod", "profile", "current", "-f", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "name": "staging,prod",
        "profiles": [
            {"name": "staging", "source": "project"},
            {"name": "prod", "source": "flag"},
        ],
        "skipped": ["ghost"],
    }


def test_profile_rename_rewrites_the_projects_that_pick_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, "profile: [staging, prod]\n")
    other = make_project(tmp_path / "PROJ-2").root
    (other / ".untaped" / "config.yml").write_text("profile: staging\n", encoding="utf-8")
    planned = _cli("profile", "rename", "staging", "stage", "--dry-run")
    assert planned.exit_code == 0, planned.output
    assert "would rewrite profile: in PROJ-1" in planned.stderr
    assert _file(root)["profile"] == ["staging", "prod"]
    result = _cli("profile", "rename", "staging", "stage")
    assert result.exit_code == 0, result.output
    assert "rewrote profile: in 2 projects" in result.stderr
    assert _file(root)["profile"] == ["stage", "prod"]
    assert _file(other)["profile"] == "stage"


def test_profile_delete_drops_the_name_from_the_projects_that_pick_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, "profile: staging\ngithub: {mode: 'on'}\n")
    preview = _cli("profile", "delete", "staging", "--dry-run")
    assert "project that picks it: PROJ-1" in preview.stdout + preview.stderr
    result = _cli("profile", "delete", "staging", "--yes")
    assert result.exit_code == 0, result.output
    assert _file(root) == {"format_version": 2, "github": {"mode": "on"}}


def test_project_init_records_the_runs_explicit_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picked = tmp_path / "T-1"
    result = _cli("--profile", "staging", "project", "init", str(picked))
    assert result.exit_code == 0, result.output
    assert _file(picked)["profile"] == "staging"
    monkeypatch.setenv("UNTAPED_PROFILE", "staging,prod")
    stacked = tmp_path / "T-2"
    assert _cli("project", "init", str(stacked)).exit_code == 0
    assert _file(stacked)["profile"] == ["staging", "prod"]


def test_project_init_without_a_pick_writes_no_profile(tmp_path: Path) -> None:
    plain = tmp_path / "T-3"
    assert _cli("project", "init", str(plain)).exit_code == 0
    assert not (plain / ".untaped" / "config.yml").exists()
    assert _cli("--profile", "default", "project", "init", str(tmp_path / "T-4")).exit_code == 0
    assert not (tmp_path / "T-4" / ".untaped" / "config.yml").exists()


def test_project_init_refuses_an_unknown_pick_before_making_anything(tmp_path: Path) -> None:
    result = _cli("--profile", "ghost", "project", "init", str(tmp_path / "T-5"))
    assert result.exit_code == 2
    assert not (tmp_path / "T-5").exists()
