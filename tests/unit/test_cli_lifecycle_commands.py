"""CLI tests for workspace lifecycle commands."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from untaped.settings import get_settings
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_init_then_list(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws-prod"
    init = runner.invoke(app, ["init", "prod", "--path", str(target)])
    assert init.exit_code == 0, init.output
    assert (target / "untaped.yml").is_file()

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert listed.exit_code == 0
    assert "prod" in listed.stdout.splitlines()


def test_init_with_branch_default(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws-prod"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "develop"])
    raw = (target / "untaped.yml").read_text()
    assert "develop" in raw


def test_init_duplicate_name_errors(tmp_path: Path) -> None:
    runner = CliInvoker()
    a = tmp_path / "a"
    b = tmp_path / "b"
    runner.invoke(app, ["init", "prod", "--path", str(a)])
    second = runner.invoke(app, ["init", "prod", "--path", str(b)])
    assert second.exit_code == 1
    assert "error:" in (second.output or second.stderr)


def test_init_default_path_uses_workspaces_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_WORKSPACE__WORKSPACES_DIR", str(tmp_path / "ws-root"))
    get_settings.cache_clear()

    result = CliInvoker().invoke(app, ["init", "prod"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ws-root" / "prod" / "untaped.yml").is_file()


def test_adopt_records_existing_clones(tmp_path: Path, existing_clones: Path) -> None:
    runner = CliInvoker()
    result = runner.invoke(app, ["adopt", str(existing_clones), "--name", "lab"])
    assert result.exit_code == 0, result.output

    manifest_text = (existing_clones / "untaped.yml").read_text()
    assert "alpha" in manifest_text
    assert "beta" in manifest_text
    assert "main" in manifest_text
    assert "trunk" in manifest_text

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "lab" in listed.stdout.splitlines()


def test_adopt_skips_dirs_without_git(tmp_path: Path, existing_clones: Path) -> None:
    (existing_clones / "notes").mkdir()
    (existing_clones / "notes" / "todo.md").write_text("hi")

    result = CliInvoker().invoke(app, ["adopt", str(existing_clones), "--name", "lab"])
    assert result.exit_code == 0, result.output

    manifest_text = (existing_clones / "untaped.yml").read_text()
    assert "notes" not in manifest_text


def test_adopt_existing_manifest_registers_without_rewriting(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    manifest_text = (
        "# existing shared manifest\n"
        "name: prod\n"
        "repos:\n"
        "  - url: https://x/api.git\n"
        "    name: api\n"
    )
    (ws_path / "untaped.yml").write_text(manifest_text)

    result = CliInvoker().invoke(app, ["adopt", str(ws_path)])

    assert result.exit_code == 0, result.output
    assert "adopted workspace 'prod'" in result.output
    assert "(1 repo)" in result.output
    assert (ws_path / "untaped.yml").read_text() == manifest_text

    listed = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert listed.stdout.splitlines() == ["prod"]


def test_adopt_existing_manifest_name_override_is_registry_only(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    manifest_text = "name: prod\nrepos: []\n"
    (ws_path / "untaped.yml").write_text(manifest_text)

    result = CliInvoker().invoke(app, ["adopt", str(ws_path), "--name", "alias"])

    assert result.exit_code == 0, result.output
    assert "adopted workspace 'alias'" in result.output
    assert (ws_path / "untaped.yml").read_text() == manifest_text


def test_adopt_existing_empty_manifest_does_not_show_discovery_hint(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = CliInvoker().invoke(app, ["adopt", str(ws_path)])

    assert result.exit_code == 0, result.output
    assert "(0 repos)" in result.output
    assert "nothing matched" not in result.output


def test_adopt_existing_manifest_duplicate_name_errors(tmp_path: Path) -> None:
    runner = CliInvoker()
    other = tmp_path / "other"
    runner.invoke(app, ["init", "prod", "--path", str(other)])

    ws_path = tmp_path / "unregistered"
    ws_path.mkdir()
    (ws_path / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = runner.invoke(app, ["adopt", str(ws_path)])

    assert result.exit_code == 1
    assert "workspace name already registered" in (result.output or result.stderr)


def test_adopt_help_mentions_existing_workspace_state() -> None:
    result = CliInvoker().invoke(app, ["adopt", "--help"])

    assert result.exit_code == 0, result.output
    assert "existing workspace state" in result.output


def test_adopt_missing_path_errors(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["adopt", str(tmp_path / "ghost"), "--name", "lab"])
    assert result.exit_code == 1
    assert "does not exist" in (result.output or result.stderr)


def test_adopt_empty_directory_hints_nothing_matched(tmp_path: Path) -> None:
    """An empty directory is a valid adopt target but the user usually
    expected something to match. Surface the empty case explicitly so
    the operation isn't silent.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliInvoker().invoke(app, ["adopt", str(empty), "--name", "lab"])
    assert result.exit_code == 0, result.output
    assert "(0 repos)" in result.output
    assert "nothing matched" in result.output


# ── forget ─────────────────────────────────────────────────────────────────


def test_forget_removes_workspace_from_registry(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "scratch", "--path", str(target)])

    forget = runner.invoke(app, ["forget", "scratch"])
    assert forget.exit_code == 0, forget.output

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "scratch" not in listed.stdout.splitlines()
    # files preserved
    assert (target / "untaped.yml").is_file()


def test_forget_unknown_workspace_errors() -> None:
    result = CliInvoker().invoke(app, ["forget", "ghost"])
    assert result.exit_code == 1


def test_forget_with_prune_deletes_workspace_dir(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "scratch", "--path", str(target)])

    forget = runner.invoke(app, ["forget", "scratch", "--prune", "--yes"])
    assert forget.exit_code == 0, forget.output
    assert not target.exists()


def test_forget_prune_aborts_on_no_at_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "scratch", "--path", str(target)])
    monkeypatch.setattr("untaped_workspace.cli.common._stdin_is_interactive", lambda: True)

    class _PromptUi:
        def confirm(self, message: str, *, default: bool = False) -> bool:
            assert "prune workspace directory" in message
            assert default is False
            return False

    monkeypatch.setattr("untaped_workspace.cli.common.ui_context", lambda **_: _PromptUi())

    forget = runner.invoke(app, ["forget", "scratch", "--prune"])
    assert forget.exit_code == 1
    assert "aborted" in forget.output
    assert target.is_dir()  # files preserved
    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "scratch" in listed.stdout.splitlines()  # registry untouched


def test_forget_prune_requires_yes_when_non_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "scratch", "--path", str(target)])
    monkeypatch.setattr("untaped_workspace.cli.common._stdin_is_interactive", lambda: False)

    forget = runner.invoke(app, ["forget", "scratch", "--prune"])

    assert forget.exit_code == 1
    assert "--yes" in forget.output
    assert target.is_dir()
    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "scratch" in listed.stdout.splitlines()


def test_forget_prune_refuses_dirty_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    runner = CliInvoker()
    target = tmp_path / "ws"
    target.mkdir()
    repo = target / "svc-a"
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--no-gpg-sign", "-m", "init"],
        check=True,
        capture_output=True,
    )
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--repo-name", "svc-a", "--workspace", "lab"])
    (repo / "f.txt").write_text("dirty")  # uncommitted

    forget = runner.invoke(app, ["forget", "lab", "--prune", "--yes"])
    assert forget.exit_code == 1
    assert target.is_dir()  # files preserved
    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "lab" in listed.stdout.splitlines()  # registry untouched


def test_forget_prune_refuses_unsafe_undeclared_child_clone(tmp_path: Path, upstream: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    clone = target / "scratch"
    subprocess.run(["git", "clone", str(upstream), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "commit.gpgsign", "false"], check=True)
    (clone / "local.txt").write_text("local")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "--no-gpg-sign", "-m", "local-only"],
        check=True,
        capture_output=True,
    )

    forget = runner.invoke(app, ["forget", "lab", "--prune", "--yes"])

    assert forget.exit_code == 1
    assert "unsafe local state" in forget.output
    assert "scratch" in forget.output
    assert target.is_dir()
    assert clone.is_dir()
    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "lab" in listed.stdout.splitlines()


# ── add / remove (manifest only) ────────────────────────────────────────────


def test_import_from_local_yaml(tmp_path: Path) -> None:
    src = tmp_path / "team-prod.yml"
    src.write_text(
        """\
name: team-prod
defaults:
  branch: main
repos:
  - url: https://github.com/org/svc-a.git
"""
    )
    dest = tmp_path / "ws-imported"
    runner = CliInvoker()
    result = runner.invoke(app, ["import", str(src), str(dest), "--name", "imported"])
    assert result.exit_code == 0, result.output
    assert (dest / "untaped.yml").is_file()

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "imported" in listed.stdout.splitlines()


def test_import_rejects_old_path_option(tmp_path: Path) -> None:
    """The ``--path`` option was dropped when dest became positional —
    invocations carrying it must surface as a usage error rather than
    silently accepting and writing to the wrong directory."""
    src = tmp_path / "m.yml"
    src.write_text("name: x\nrepos: []\n")
    dest = tmp_path / "ws"
    result = CliInvoker().invoke(app, ["import", str(src), "--path", str(dest)])
    assert result.exit_code != 0


def test_import_sync_scopes_to_imported_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``import --sync`` must pass ``only=`` matching the imported
    manifest's repo names — same contract as ``add --sync``. Pinned
    here so a future change can't silently revert to the
    "sync the whole manifest" shape."""
    captured: dict[str, object] = {}

    class _StubSync:
        def __init__(
            self,
            manifests: object,
            git: object,
            *,
            fs: object,
            cache_dir: object,
        ) -> None:
            pass

        def __call__(self, ws: object, *, only: object = None) -> list[object]:
            captured["only"] = only
            return []

    monkeypatch.setattr("untaped_workspace.cli.lifecycle_commands.SyncWorkspace", _StubSync)

    src = tmp_path / "m.yml"
    src.write_text(
        """\
name: imported
repos:
  - url: https://github.com/org/svc-a.git
  - url: https://github.com/org/svc-b.git
    name: beta
"""
    )
    dest = tmp_path / "ws"
    result = CliInvoker().invoke(app, ["import", str(src), str(dest), "--sync"])
    assert result.exit_code == 0, result.output
    assert tuple(captured["only"]) == ("svc-a", "beta")


# ── add / path --stdin pipeline shape (issue #154) ──────────────────────────
