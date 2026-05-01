"""CLI smoke + workflow tests for `untaped workspace`."""

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner
from untaped_core.settings import get_settings
from untaped_workspace import app


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


# ── help / no-args ──────────────────────────────────────────────────────────


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "list",
        "init",
        "add",
        "remove",
        "sync",
        "status",
        "foreach",
        "import",
        "path",
        "shell-init",
        "edit",
    ):
        assert cmd in result.stdout


@pytest.mark.parametrize(
    "cmd", ["init", "add", "remove", "foreach", "import", "path", "shell-init", "edit"]
)
def test_no_args_shows_help(cmd: str) -> None:
    result = CliRunner().invoke(app, [cmd])
    # no_args_is_help: exit 0 (help) or 2 (Click's missing arg)
    assert result.exit_code in (0, 2)


# ── init / list ─────────────────────────────────────────────────────────────


def test_init_then_list(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws-prod"
    init = runner.invoke(app, ["init", str(target), "--name", "prod"])
    assert init.exit_code == 0, init.output
    assert (target / "untaped.yml").is_file()

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert listed.exit_code == 0
    assert "prod" in listed.stdout.splitlines()


def test_init_with_branch_default(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws-prod"
    runner.invoke(app, ["init", str(target), "--branch", "develop"])
    raw = (target / "untaped.yml").read_text()
    assert "develop" in raw


def test_init_duplicate_name_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    a = tmp_path / "a"
    b = tmp_path / "b"
    runner.invoke(app, ["init", str(a), "--name", "prod"])
    second = runner.invoke(app, ["init", str(b), "--name", "prod"])
    assert second.exit_code == 1
    assert "error:" in (second.output or second.stderr)


# ── add / remove (manifest only) ────────────────────────────────────────────


def test_add_then_remove(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "lab"])

    a = runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])
    assert a.exit_code == 0, a.output

    rm = runner.invoke(app, ["remove", "svc-a", "--name", "lab"])
    assert rm.exit_code == 0, rm.output


def test_add_unknown_workspace_errors(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["add", "https://x/a.git", "--name", "ghost"])
    assert result.exit_code == 1


def test_remove_accepts_multiple_repos(tmp_path: Path) -> None:
    """Repeated positional repo identifiers — drop several manifests entries
    in one call."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "lab"])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])
    runner.invoke(app, ["add", "https://x/svc-b.git", "--name", "lab"])

    rm = runner.invoke(app, ["remove", "svc-a", "svc-b", "--name", "lab"])
    assert rm.exit_code == 0, rm.output


def test_remove_reads_repos_from_stdin(tmp_path: Path) -> None:
    """`workspace list ... | remove --stdin` is the documented pipeline shape."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "lab"])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])
    runner.invoke(app, ["add", "https://x/svc-b.git", "--name", "lab"])

    rm = runner.invoke(app, ["remove", "--stdin", "--name", "lab"], input="svc-a\nsvc-b\n")
    assert rm.exit_code == 0, rm.output


# ── path / edit / shell-init ────────────────────────────────────────────────


def test_path_prints_workspace_path(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws-prod"
    runner.invoke(app, ["init", str(target), "--name", "prod"])
    out = runner.invoke(app, ["path", "prod"])
    assert out.exit_code == 0
    assert out.stdout.strip() == str(target.resolve())


def test_path_unknown_workspace_errors() -> None:
    result = CliRunner().invoke(app, ["path", "ghost"])
    assert result.exit_code == 1


def test_shell_init_zsh() -> None:
    result = CliRunner().invoke(app, ["shell-init", "zsh"])
    assert result.exit_code == 0
    assert "uwcd()" in result.stdout


def test_shell_init_fish() -> None:
    result = CliRunner().invoke(app, ["shell-init", "fish"])
    assert result.exit_code == 0
    assert "function uwcd" in result.stdout


def test_shell_init_unknown() -> None:
    result = CliRunner().invoke(app, ["shell-init", "powershell"])
    assert result.exit_code == 1


def test_edit_uses_explicit_editor(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "prod"])
    # `true` is a real binary that exits 0 — perfect smoke test
    result = runner.invoke(app, ["edit", "prod", "--editor", "true"])
    assert result.exit_code == 0


# ── sync / status / foreach (real git) ──────────────────────────────────────


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    bare = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "_seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "tag.gpgsign", "false"], check=True)
    (seed / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True
    )
    shutil.rmtree(seed)
    return bare


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "_cache"
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(cache))
    get_settings.cache_clear()
    return cache


def test_sync_clones_repos(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "smoke"])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])

    result = runner.invoke(
        app,
        ["sync", "--name", "smoke", "--format", "raw", "--columns", "repo", "--columns", "action"],
    )
    assert result.exit_code == 0, result.output
    assert "clone" in result.stdout
    assert (target / "upstream").is_dir()


def test_status_after_sync(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "smoke"])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(
        app,
        [
            "status",
            "--name",
            "smoke",
            "--format",
            "raw",
            "--columns",
            "repo",
            "--columns",
            "branch",
        ],
    )
    assert result.exit_code == 0
    assert "upstream\tmain" in result.stdout


def test_foreach_runs_command_in_each_repo(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "smoke"])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(app, ["foreach", "git rev-parse --abbrev-ref HEAD", "--name", "smoke"])
    assert result.exit_code == 0, result.output
    # Output is prefixed `[upstream] main`
    assert "[upstream] main" in result.stdout


def test_remove_prune_with_yes(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target), "--name", "smoke"])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])
    assert (target / "upstream").is_dir()

    rm = runner.invoke(app, ["remove", "upstream", "--name", "smoke", "--prune", "--yes"])
    assert rm.exit_code == 0, rm.output
    assert not (target / "upstream").exists()


# ── import ──────────────────────────────────────────────────────────────────


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
    runner = CliRunner()
    result = runner.invoke(app, ["import", str(src), "--path", str(dest), "--name", "imported"])
    assert result.exit_code == 0, result.output
    assert (dest / "untaped.yml").is_file()

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "imported" in listed.stdout.splitlines()
