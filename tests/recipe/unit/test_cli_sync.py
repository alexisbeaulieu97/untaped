"""CLI tests for ``untaped recipe sync`` (re-fetch installed packs from their source)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import untaped.capabilities.recipe.cli.library_commands as library_commands
from untaped.capabilities.recipe.cli import app
from untaped.capabilities.recipe.cli.common import library_root
from untaped.capabilities.recipe.infrastructure.pack_store import PackLibrary
from untaped.testing import CliInvoker, ScriptedPromptBackend, invoke_cli

pytestmark = pytest.mark.usefixtures("isolate_config")


def _write_pack(root: Path, *, name: str, body: str = "version: 1\nsteps: []\n") -> None:
    recipe = root / "recipes" / "seed.yml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text(body)
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        '"seed" = { path = "recipes/seed.yml" }\n'
    )


def _add(source: Path) -> None:
    result = CliInvoker().invoke(app, ["add", str(source)])
    assert result.exit_code == 0, result.output


def _installed_recipe(name: str) -> Path:
    return library_root() / "packs" / name / "recipes" / "seed.yml"


_CHANGED = "version: 1\ndescription: changed\nsteps: []\n"


def test_sync_reports_unchanged_packs_without_prompting(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")

    result = invoke_cli(app, ["sync", "alpha", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"action": "unchanged", "name": "alpha", "source": str(tmp_path / "alpha"), "rev": None}
    ]


def test_sync_updates_changed_packs_after_confirmation(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        _write_pack(tmp_path / name, name=name)
        _add(tmp_path / name)
    (tmp_path / "beta" / "recipes" / "seed.yml").write_text(_CHANGED)
    backend = ScriptedPromptBackend(confirms=[True])

    result = invoke_cli(
        app, ["sync", "--all", "--format", "json"], terminal=True, prompt_backend=backend
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(row["name"], row["action"]) for row in rows] == [
        ("alpha", "unchanged"),
        ("beta", "updated"),
    ]
    assert "About to sync 1 pack:" in result.stderr
    assert _installed_recipe("beta").read_text() == _CHANGED


def test_sync_dry_run_plans_and_changes_nothing(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["sync", "alpha", "--dry-run", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "planned"
    assert _installed_recipe("alpha").read_text() != _CHANGED


def test_sync_decline_changes_nothing(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)
    backend = ScriptedPromptBackend(confirms=[False])

    result = invoke_cli(app, ["sync", "alpha"], terminal=True, prompt_backend=backend)

    assert result.exit_code == 1, result.output
    assert "cancelled; no changes made" in result.stderr
    assert _installed_recipe("alpha").read_text() != _CHANGED


def test_sync_requires_yes_without_a_terminal(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["sync", "alpha"])

    assert result.exit_code == 2, result.output
    assert "sync requires --yes when not interactive" in result.stderr


def test_sync_keeps_local_edits_unless_discarded(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)
    _installed_recipe("alpha").write_text("version: 1\ndescription: mine\nsteps: []\n")

    kept = invoke_cli(app, ["sync", "alpha", "--yes"])
    discarded = invoke_cli(app, ["sync", "alpha", "--yes", "--discard-edits"])

    assert kept.exit_code == 1, kept.output
    assert "error: alpha: pack 'alpha' has local edits" in kept.stderr
    assert discarded.exit_code == 0, discarded.output
    assert _installed_recipe("alpha").read_text() == _CHANGED


def test_sync_reports_a_missing_source_and_continues(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        _write_pack(tmp_path / name, name=name)
        _add(tmp_path / name)
    shutil.rmtree(tmp_path / "alpha")
    (tmp_path / "beta" / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["sync", "--all", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert f"error: alpha: pack source not found: {tmp_path / 'alpha'}" in result.stderr
    assert [row["name"] for row in json.loads(result.stdout)] == ["beta"]


def test_add_records_a_relative_path_source_as_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    monkeypatch.chdir(tmp_path)
    added = CliInvoker().invoke(app, ["add", "./alpha", "--format", "json"])
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = invoke_cli(app, ["sync", "alpha", "--yes", "--format", "json"])

    assert added.exit_code == 0, added.output
    assert json.loads(added.stdout)["source"] == str(tmp_path / "alpha")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "updated"


def test_sync_refetches_git_sources_at_the_recorded_rev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = tmp_path / "upstream"
    _write_pack(upstream, name="alpha")
    fetched: list[tuple[str, str | None]] = []

    def fake_fetch(url: str, *, rev: str | None, dest: Path) -> Path:
        fetched.append((url, rev))
        shutil.copytree(upstream, dest)
        return dest

    monkeypatch.setattr(library_commands, "fetch_pack_source", fake_fetch)
    url = "https://example.test/alpha.git"
    added = CliInvoker().invoke(app, ["add", url, "--rev", "v1"])
    assert added.exit_code == 0, added.output
    (upstream / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["sync", "alpha", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert fetched == [(url, "v1"), (url, "v1")]
    assert json.loads(result.stdout) == [
        {"action": "updated", "name": "alpha", "source": url, "rev": "v1"}
    ]
    assert _installed_recipe("alpha").read_text() == _CHANGED


@pytest.mark.parametrize(
    "args",
    [["sync"], ["sync", "alpha", "--all"]],
)
def test_sync_needs_names_or_all(args: list[str]) -> None:
    result = invoke_cli(app, args)

    assert result.exit_code == 2, result.output


def test_sync_unknown_pack_fails(tmp_path: Path) -> None:
    result = invoke_cli(app, ["sync", "ghost", "--yes"])

    assert result.exit_code == 1, result.output
    assert "pack not found: 'ghost'" in result.stderr


def test_sync_refuses_a_legacy_relative_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    # Older installs recorded a local source as typed, relative to the shell.
    PackLibrary(library_root=library_root()).add(
        tmp_path / "alpha", source="alpha", rev=None, name="alpha", force=False
    )
    elsewhere = tmp_path / "elsewhere"
    _write_pack(elsewhere / "alpha", name="alpha", body=_CHANGED)
    monkeypatch.chdir(elsewhere)

    result = invoke_cli(app, ["sync", "alpha", "--yes"])

    assert result.exit_code == 1, result.output
    assert "recorded source 'alpha' is a relative path" in result.stderr
    assert _installed_recipe("alpha").read_text() != _CHANGED
