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
        "adopt",
        "forget",
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
    "cmd",
    [
        "init",
        "adopt",
        "forget",
        "add",
        "remove",
        "foreach",
        "import",
        "path",
        "shell-init",
        "edit",
    ],
)
def test_no_args_shows_help(cmd: str) -> None:
    result = CliRunner().invoke(app, [cmd])
    # no_args_is_help: exit 0 (help) or 2 (Click's missing arg)
    assert result.exit_code in (0, 2)


# ── init / list ─────────────────────────────────────────────────────────────


def test_init_then_list(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws-prod"
    init = runner.invoke(app, ["init", "prod", "--path", str(target)])
    assert init.exit_code == 0, init.output
    assert (target / "untaped.yml").is_file()

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert listed.exit_code == 0
    assert "prod" in listed.stdout.splitlines()


def test_init_with_branch_default(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws-prod"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "develop"])
    raw = (target / "untaped.yml").read_text()
    assert "develop" in raw


def test_init_duplicate_name_errors(tmp_path: Path) -> None:
    runner = CliRunner()
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

    result = CliRunner().invoke(app, ["init", "prod"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ws-root" / "prod" / "untaped.yml").is_file()


# ── adopt ───────────────────────────────────────────────────────────────────


@pytest.fixture
def existing_clones(tmp_path: Path) -> Path:
    """A directory pre-populated with two real git clones on different branches."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    upstream_a = tmp_path / "_up_a.git"
    upstream_b = tmp_path / "_up_b.git"
    for upstream, branch in ((upstream_a, "main"), (upstream_b, "trunk")):
        subprocess.run(
            ["git", "init", "--bare", f"--initial-branch={branch}", str(upstream)],
            check=True,
            capture_output=True,
        )
        seed = tmp_path / f"_seed_{upstream.name}"
        subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
        (seed / "README.md").write_text("hi")
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "init"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "push", "origin", branch],
            check=True,
            capture_output=True,
        )
        shutil.rmtree(seed)

    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(
        ["git", "clone", "--branch", "main", str(upstream_a), str(ws / "alpha")],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", "--branch", "trunk", str(upstream_b), str(ws / "beta")],
        check=True,
        capture_output=True,
    )
    return ws


def test_adopt_records_existing_clones(tmp_path: Path, existing_clones: Path) -> None:
    runner = CliRunner()
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

    result = CliRunner().invoke(app, ["adopt", str(existing_clones), "--name", "lab"])
    assert result.exit_code == 0, result.output

    manifest_text = (existing_clones / "untaped.yml").read_text()
    assert "notes" not in manifest_text


def test_adopt_refuses_when_manifest_exists(tmp_path: Path, existing_clones: Path) -> None:
    (existing_clones / "untaped.yml").write_text("name: prior\nrepos: []\n")
    result = CliRunner().invoke(app, ["adopt", str(existing_clones), "--name", "lab"])
    assert result.exit_code == 1
    assert "already initialised" in (result.output or result.stderr)


def test_adopt_missing_path_errors(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["adopt", str(tmp_path / "ghost"), "--name", "lab"])
    assert result.exit_code == 1
    assert "does not exist" in (result.output or result.stderr)


# ── forget ─────────────────────────────────────────────────────────────────


def test_forget_removes_workspace_from_registry(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "scratch", "--path", str(target)])

    forget = runner.invoke(app, ["forget", "scratch"])
    assert forget.exit_code == 0, forget.output

    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "scratch" not in listed.stdout.splitlines()
    # files preserved
    assert (target / "untaped.yml").is_file()


def test_forget_unknown_workspace_errors() -> None:
    result = CliRunner().invoke(app, ["forget", "ghost"])
    assert result.exit_code == 1


def test_forget_with_prune_deletes_workspace_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "scratch", "--path", str(target)])

    forget = runner.invoke(app, ["forget", "scratch", "--prune", "--yes"])
    assert forget.exit_code == 0, forget.output
    assert not target.exists()


def test_forget_prune_refuses_dirty_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    runner = CliRunner()
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
    runner.invoke(app, ["add", "https://x/svc-a.git", "--repo-name", "svc-a", "--name", "lab"])
    (repo / "f.txt").write_text("dirty")  # uncommitted

    forget = runner.invoke(app, ["forget", "lab", "--prune", "--yes"])
    assert forget.exit_code == 1
    assert target.is_dir()  # files preserved
    listed = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert "lab" in listed.stdout.splitlines()  # registry untouched


# ── add / remove (manifest only) ────────────────────────────────────────────


def test_add_then_remove(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])

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
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])
    runner.invoke(app, ["add", "https://x/svc-b.git", "--name", "lab"])

    rm = runner.invoke(app, ["remove", "svc-a", "svc-b", "--name", "lab"])
    assert rm.exit_code == 0, rm.output


def test_remove_reads_repos_from_stdin(tmp_path: Path) -> None:
    """`workspace list ... | remove --stdin` is the documented pipeline shape."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])
    runner.invoke(app, ["add", "https://x/svc-b.git", "--name", "lab"])

    rm = runner.invoke(app, ["remove", "--stdin", "--name", "lab"], input="svc-a\nsvc-b\n")
    assert rm.exit_code == 0, rm.output


def test_remove_continues_when_one_repo_missing(tmp_path: Path) -> None:
    """A missing repo in a multi-repo `remove` batch must not suppress
    the removals that resolved successfully — same pipeline-resilience
    rule as ``awx <kind> get --stdin`` and ``launch --stdin``."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])

    rm = runner.invoke(app, ["remove", "ghost", "svc-a", "--name", "lab"])
    assert rm.exit_code != 0
    # svc-a was removed despite ghost failing — confirmed by being able to
    # re-add it without "duplicate" errors.
    re_add = runner.invoke(app, ["add", "https://x/svc-a.git", "--name", "lab"])
    assert re_add.exit_code == 0, re_add.output


# ── path / edit / shell-init ────────────────────────────────────────────────


def test_path_prints_workspace_path(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws-prod"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
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
    runner.invoke(app, ["init", "prod", "--path", str(target)])
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
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
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
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
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
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(app, ["foreach", "git rev-parse --abbrev-ref HEAD", "--name", "smoke"])
    assert result.exit_code == 0, result.output
    # Output is prefixed `[upstream] main`
    assert "[upstream] main" in result.stdout


def test_foreach_structured_format(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    """`--format json` emits ForeachOutcome rows; the [repo]-prefixed
    passthrough is suppressed so downstream tools can parse stdout."""
    import json as _json

    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(
        app,
        [
            "foreach",
            "git rev-parse --abbrev-ref HEAD",
            "--name",
            "smoke",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed
    row = parsed[0]
    assert {
        "workspace",
        "repo",
        "command",
        "returncode",
        "stdout",
        "stderr",
        "duration_s",
    } <= set(row)
    assert row["repo"] == "upstream"
    assert row["command"] == "git rev-parse --abbrev-ref HEAD"
    assert row["duration_s"] >= 0.0
    assert "[upstream]" not in result.stdout


def test_foreach_format_raw_columns(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    """`--format raw --columns repo,returncode` produces tab-separated rows."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(
        app,
        [
            "foreach",
            "git rev-parse --abbrev-ref HEAD",
            "--name",
            "smoke",
            "--format",
            "raw",
            "--columns",
            "repo",
            "--columns",
            "returncode",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "upstream\t0" in result.stdout
    assert "[upstream]" not in result.stdout


def test_foreach_default_emits_summary_on_failure(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """Even in default fail-fast mode, a failing repo surfaces in the summary line."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--name", "smoke"])
    assert result.exit_code == 1
    assert "failed in: upstream" in (result.stderr or result.output)


def test_foreach_continue_on_error_still_exits_one(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """`--continue-on-error` keeps going but still exits 1 on failures
    (pins the historical contract)."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--name", "smoke", "--continue-on-error"])
    assert result.exit_code == 1
    assert "failed in: upstream" in (result.stderr or result.output)


def test_foreach_ignore_errors_exits_zero_with_summary(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """`--ignore-errors` keeps going AND exits 0; failures surface via the
    summary line so they aren't silent."""
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--name", "smoke", "--ignore-errors"])
    assert result.exit_code == 0, result.output
    assert "failed in: upstream" in (result.stderr or result.output)


def test_foreach_summary_suppressed_in_structured_format(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """Machine formats stay clean — failures are conveyed by `returncode`
    on each row, not by the human summary line."""
    import json as _json

    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--name", "smoke"])
    runner.invoke(app, ["sync", "--name", "smoke"])

    result = runner.invoke(
        app,
        ["foreach", "false", "--name", "smoke", "--ignore-errors", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed
    assert any(row["returncode"] != 0 for row in parsed)
    assert "failed in:" not in (result.stderr or "")


def test_remove_prune_with_yes(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
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
