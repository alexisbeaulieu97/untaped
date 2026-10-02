"""CLI tests for ``untaped recipe packs sync`` (re-fetch installed packs from their source)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import untaped_recipe.cli.library_commands as library_commands
from untaped.testing import CliInvoker, ScriptedPromptBackend, invoke_cli
from untaped_recipe.cli import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.infrastructure.pack_store import PackLibrary

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
    result = CliInvoker().invoke(app, ["packs", "add", str(source)])
    assert result.exit_code == 0, result.output


def _installed_recipe(name: str) -> Path:
    return library_root() / "packs" / name / "recipes" / "seed.yml"


_CHANGED = "version: 1\ndescription: changed\nsteps: []\n"
_OLD_SHA = "1111111111111111111111111111111111111111"
_NEW_SHA = "2222222222222222222222222222222222222222"


def test_sync_reports_unchanged_packs_without_prompting(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")

    result = invoke_cli(app, ["packs", "sync", "alpha", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "action": "unchanged",
            "name": "alpha",
            "source": str(tmp_path / "alpha"),
            "rev": None,
            "commit": None,
            "detail": None,
        }
    ]


def test_sync_updates_changed_packs_after_confirmation(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        _write_pack(tmp_path / name, name=name)
        _add(tmp_path / name)
    (tmp_path / "beta" / "recipes" / "seed.yml").write_text(_CHANGED)
    backend = ScriptedPromptBackend(confirms=[True])

    result = invoke_cli(
        app, ["packs", "sync", "--all", "--format", "json"], terminal=True, prompt_backend=backend
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

    result = invoke_cli(app, ["packs", "sync", "alpha", "--dry-run", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "planned"
    assert _installed_recipe("alpha").read_text() != _CHANGED


def test_sync_decline_changes_nothing(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)
    backend = ScriptedPromptBackend(confirms=[False])

    result = invoke_cli(app, ["packs", "sync", "alpha"], terminal=True, prompt_backend=backend)

    assert result.exit_code == 1, result.output
    assert "cancelled; no changes made" in result.stderr
    assert _installed_recipe("alpha").read_text() != _CHANGED


def test_sync_requires_yes_without_a_terminal(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["packs", "sync", "alpha"])

    assert result.exit_code == 2, result.output
    assert "sync requires --yes when not interactive" in result.stderr


def test_sync_keeps_local_edits_unless_discarded(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)
    _installed_recipe("alpha").write_text("version: 1\ndescription: mine\nsteps: []\n")

    kept = invoke_cli(app, ["packs", "sync", "alpha", "--yes"])
    discarded = invoke_cli(app, ["packs", "sync", "alpha", "--yes", "--discard-edits"])

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

    result = invoke_cli(app, ["packs", "sync", "--all", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    message = f"pack source not found: {tmp_path / 'alpha'}"
    assert result.stderr.count(f"error: alpha: {message}") == 1
    rows = json.loads(result.stdout)
    assert [(row["name"], row["action"]) for row in rows] == [
        ("alpha", "failed"),
        ("beta", "updated"),
    ]
    assert (rows[0]["source"], rows[0]["detail"]) == (str(tmp_path / "alpha"), message)
    assert rows[0]["error"]["category"] == "not_found"
    assert "error" not in rows[1]


def test_sync_install_failure_is_a_failed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("alpha", "beta"):
        _write_pack(tmp_path / name, name=name)
        _add(tmp_path / name)
        (tmp_path / name / "recipes" / "seed.yml").write_text(_CHANGED)
    original_add = PackLibrary.add

    def _flaky_add(self: PackLibrary, source_dir: Path, **kwargs: object) -> None:
        if kwargs.get("name") == "alpha":
            raise OSError("disk full")
        original_add(self, source_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PackLibrary, "add", _flaky_add)
    result = invoke_cli(app, ["packs", "sync", "--all", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert result.stderr.count("error: alpha: disk full") == 1
    rows = json.loads(result.stdout)
    assert [(row["name"], row["action"]) for row in rows] == [
        ("alpha", "failed"),
        ("beta", "updated"),
    ]
    assert rows[0]["detail"] == "disk full"
    assert rows[0]["error"]["category"] == "failed"


def test_add_records_a_relative_path_source_as_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    monkeypatch.chdir(tmp_path)
    added = CliInvoker().invoke(app, ["packs", "add", "./alpha", "--format", "json"])
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = invoke_cli(app, ["packs", "sync", "alpha", "--yes", "--format", "json"])

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
    commits = iter([_OLD_SHA, _NEW_SHA])
    monkeypatch.setattr(library_commands, "checkout_commit", lambda checkout: next(commits))
    url = "https://example.test/alpha.git"
    added = CliInvoker().invoke(app, ["packs", "add", url, "--rev", "v1", "--format", "json"])
    assert added.exit_code == 0, added.output
    (upstream / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["packs", "sync", "alpha", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert fetched == [(url, "v1"), (url, "v1")]
    assert json.loads(added.stdout)["commit"] == _OLD_SHA
    assert json.loads(result.stdout) == [
        {
            "action": "updated",
            "name": "alpha",
            "source": url,
            "rev": "v1",
            "commit": _NEW_SHA,
            "detail": None,
        }
    ]
    assert _installed_recipe("alpha").read_text() == _CHANGED
    assert PackLibrary(library_root=library_root()).packs()[0].commit == _NEW_SHA


def _add_hook(root: Path, body: str = "    return content\n") -> None:
    module = root / "src" / "alpha_hooks" / "tweak.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    (module.parent / "__init__.py").write_text("")
    module.write_text("def transform(content, *, inputs, target, file, args, helpers):\n" + body)
    (root / "uv.lock").write_text("version = 1\n")
    with (root / "pyproject.toml").open("a") as pyproject:
        pyproject.write(
            '\n[tool.untaped_recipe.hooks]\n"tweak" = { module = "alpha_hooks.tweak" }\n'
        )


def test_sync_confirmation_shows_the_commit_move_and_changed_hook_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = tmp_path / "upstream"
    _write_pack(upstream, name="alpha")
    _add_hook(upstream)

    def fake_fetch(url: str, *, rev: str | None, dest: Path) -> Path:
        shutil.copytree(upstream, dest)
        return dest

    monkeypatch.setattr(library_commands, "fetch_pack_source", fake_fetch)
    commits = iter([_OLD_SHA, _NEW_SHA, _NEW_SHA])
    monkeypatch.setattr(library_commands, "checkout_commit", lambda checkout: next(commits))
    url = "https://example.test/alpha.git"
    assert CliInvoker().invoke(app, ["packs", "add", url, "--rev", "main"]).exit_code == 0
    module = upstream / "src" / "alpha_hooks" / "tweak.py"
    module.write_text(module.read_text().replace("return content", "return content + 'x'"))
    backend = ScriptedPromptBackend(confirms=[False])

    dry_run = invoke_cli(app, ["packs", "sync", "alpha", "--dry-run"])
    declined = invoke_cli(app, ["packs", "sync", "alpha"], terminal=True, prompt_backend=backend)

    for result in (dry_run, declined):
        assert f"  - alpha from {url}@main ({_OLD_SHA[:12]} -> {_NEW_SHA[:12]})" in result.stderr
        assert "    hook code changed: src/alpha_hooks/tweak.py" in result.stderr
    assert dry_run.exit_code == 0, dry_run.output
    assert declined.exit_code == 1, declined.output
    assert "cancelled; no changes made" in declined.stderr
    installed = library_root() / "packs" / "alpha" / "src" / "alpha_hooks" / "tweak.py"
    assert "'x'" not in installed.read_text()


@pytest.mark.parametrize("recorded", [None, _OLD_SHA])
def test_sync_records_a_moved_commit_when_content_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: str | None
) -> None:
    upstream = tmp_path / "upstream"
    _write_pack(upstream, name="alpha")
    url = "https://example.test/alpha.git"
    # None: a row written before commits were recorded.
    PackLibrary(library_root=library_root()).add(
        upstream, source=url, rev="main", commit=recorded, name="alpha", force=False
    )

    def fake_fetch(url: str, *, rev: str | None, dest: Path) -> Path:
        shutil.copytree(upstream, dest)
        return dest

    monkeypatch.setattr(library_commands, "fetch_pack_source", fake_fetch)
    monkeypatch.setattr(library_commands, "checkout_commit", lambda checkout: _NEW_SHA)

    dry_run = invoke_cli(app, ["packs", "sync", "alpha", "--dry-run", "--format", "json"])
    unchanged_commit = PackLibrary(library_root=library_root()).packs()[0].commit
    result = invoke_cli(app, ["packs", "sync", "alpha", "--format", "json"])

    assert dry_run.exit_code == 0, dry_run.output
    assert unchanged_commit == (recorded or "")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0] == {
        "action": "unchanged",
        "name": "alpha",
        "source": url,
        "rev": "main",
        "commit": _NEW_SHA,
        "detail": None,
    }
    assert PackLibrary(library_root=library_root()).packs()[0].commit == _NEW_SHA


def test_sync_preview_says_when_hook_code_is_unchanged(tmp_path: Path) -> None:
    _write_pack(tmp_path / "alpha", name="alpha")
    _add_hook(tmp_path / "alpha")
    _add(tmp_path / "alpha")
    (tmp_path / "alpha" / "recipes" / "seed.yml").write_text(_CHANGED)

    result = invoke_cli(app, ["packs", "sync", "alpha", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"  - alpha from {tmp_path / 'alpha'}\n    hook code unchanged" in result.stderr


@pytest.mark.parametrize(
    "args",
    [["packs", "sync"], ["packs", "sync", "alpha", "--all"]],
)
def test_sync_needs_names_or_all(args: list[str]) -> None:
    result = invoke_cli(app, args)

    assert result.exit_code == 2, result.output


def test_sync_unknown_pack_fails(tmp_path: Path) -> None:
    result = invoke_cli(app, ["packs", "sync", "ghost", "--yes"])

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

    result = invoke_cli(app, ["packs", "sync", "alpha", "--yes"])

    assert result.exit_code == 1, result.output
    assert "recorded source 'alpha' is a relative path" in result.stderr
    assert _installed_recipe("alpha").read_text() != _CHANGED
