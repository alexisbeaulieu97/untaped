"""CLI tests for workspace sync, status, and foreach commands."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from untaped import bootstrap
from untaped.capabilities.workspace import SPEC
from untaped.capabilities.workspace.cli import app
from untaped.capabilities.workspace.infrastructure import InterruptibleShellRunner
from untaped.testing import CliInvoker, ScriptedPromptBackend

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_sync_repo_filter_limits_cloned_repos(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    other_upstream = tmp_path / "other.git"
    shutil.copytree(upstream, other_upstream)
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(
        app,
        ["add", f"file://{other_upstream}", "--repo-name", "ui", "--workspace", "smoke"],
    )

    result = runner.invoke(
        app,
        [
            "sync",
            "--workspace",
            "smoke",
            "--repo",
            "upstream",
            "--format",
            "raw",
            "--columns",
            "repo",
            "--columns",
            "action",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["upstream\tcloned"]
    assert (target / "upstream").is_dir()
    assert not (target / "ui").exists()


def test_sync_failed_clone_is_failed_row_and_exit_one(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(
        app,
        [
            "add",
            f"file://{tmp_path / 'missing.git'}",
            "--repo-name",
            "gone",
            "--workspace",
            "smoke",
        ],
    )

    result = runner.invoke(app, ["sync", "--workspace", "smoke", "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = {row["repo"]: row for row in json.loads(result.stdout)}
    assert rows["upstream"]["action"] == "cloned"
    assert rows["gone"]["action"] == "failed"
    assert rows["gone"]["detail"].startswith("cache fetch failed: git clone failed: ")
    assert str(isolated_cache) not in rows["gone"]["detail"]
    assert "1 failed" in result.stderr


def _workspace_with_safe_orphan(tmp_path: Path, upstream: Path) -> Path:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    synced = runner.invoke(app, ["sync", "--workspace", "smoke"])
    assert synced.exit_code == 0, synced.output
    removed = runner.invoke(app, ["remove", "upstream", "--workspace", "smoke"])
    assert removed.exit_code == 0, removed.output
    return target / "upstream"


def test_sync_prune_requires_yes_when_non_interactive(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    orphan = _workspace_with_safe_orphan(tmp_path, upstream)

    result = CliInvoker().invoke(app, ["sync", "--workspace", "smoke", "--prune"])

    assert result.exit_code == 2, result.output
    assert "--yes" in result.output
    assert orphan.is_dir()


def test_sync_prune_with_yes_removes_safe_orphan(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    orphan = _workspace_with_safe_orphan(tmp_path, upstream)

    result = CliInvoker().invoke(
        app, ["sync", "--workspace", "smoke", "--prune", "--yes", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "smoke",
            "repo": "upstream",
            "action": "removed",
            "detail": "no longer declared",
            "target_path": str(orphan.resolve()),
        }
    ]
    assert not orphan.exists()


def test_sync_prune_decline_keeps_orphan(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    orphan = _workspace_with_safe_orphan(tmp_path, upstream)
    backend = ScriptedPromptBackend(confirms=[False])

    result = CliInvoker().invoke(
        app,
        ["sync", "--workspace", "smoke", "--prune"],
        interactive=True,
        prompt_backend=backend,
    )

    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Continue?")]
    assert str(orphan) in result.output
    assert orphan.is_dir()


def test_sync_all_repo_filter_emits_warning_and_per_workspace_outcomes(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """``sync --all --repo`` filters per-workspace: workspaces with the
    requested repo sync it; workspaces without emit ``unmatched`` rows.
    A stderr warning notifies the user that relaxed semantics are
    active. Single-workspace ``--repo`` still raises (covered by the
    use-case unit tests); this test covers the CLI wiring.
    """
    runner = CliInvoker()

    # Workspace alpha: has the upstream repo.
    ws_alpha = tmp_path / "ws-alpha"
    runner.invoke(app, ["init", "alpha", "--path", str(ws_alpha)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "alpha"])

    # Workspace beta: empty manifest — does NOT have upstream.
    ws_beta = tmp_path / "ws-beta"
    runner.invoke(app, ["init", "beta", "--path", str(ws_beta)])

    result = runner.invoke(
        app,
        [
            "sync",
            "--all",
            "--repo",
            "upstream",
            "--format",
            "raw",
            "--columns",
            "workspace",
            "--columns",
            "repo",
            "--columns",
            "action",
        ],
    )
    assert result.exit_code == 0, result.output

    # Stderr warning should mention relaxed semantics. ``CliInvoker``
    # mixes stderr into ``output`` by default, so inspect the combined
    # surface.
    assert "warning" in result.output.lower()
    assert "--all --repo" in result.output

    # Stdout rows: alpha synced upstream (clone), beta produced
    # an unmatched row for upstream.
    rows = [r for r in result.output.strip().splitlines() if "\t" in r]
    assert "alpha\tupstream\tcloned" in rows, rows
    assert "beta\tupstream\tunmatched" in rows, rows


def test_sync_parallel_single_workspace_uses_repo_workers_in_manifest_order(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """``sync -j N`` works for a single workspace and keeps manifest order
    even when repo names would sort differently alphabetically."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    other_upstream = tmp_path / "other.git"
    shutil.copytree(upstream, other_upstream)
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(
        app,
        ["add", f"file://{upstream}", "--repo-name", "z-repo", "--workspace", "prod"],
    )
    runner.invoke(
        app,
        ["add", f"file://{other_upstream}", "--repo-name", "a-repo", "--workspace", "prod"],
    )

    result = runner.invoke(
        app,
        [
            "sync",
            "--workspace",
            "prod",
            "-j",
            "2",
            "--format",
            "raw",
            "--columns",
            "repo",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["z-repo", "a-repo"]
    assert "syncing 2 repos with up to 2 workers" in result.output
    assert "sync: 2 cloned" in result.output


def test_sync_quiet_suppresses_progress_and_summary(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    other_upstream = tmp_path / "other.git"
    shutil.copytree(upstream, other_upstream)
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(
        app,
        ["add", f"file://{other_upstream}", "--repo-name", "ui", "--workspace", "smoke"],
    )

    bootstrap._clear_for_tests()
    try:
        root = bootstrap.build_root_app(builtins=(SPEC,), externals=())
        result = runner.invoke(
            root.meta,
            [
                "workspace",
                "sync",
                "--quiet",
                "--workspace",
                "smoke",
                "-j",
                "2",
                "--format",
                "raw",
                "--columns",
                "repo",
            ],
        )
    finally:
        bootstrap._clear_for_tests()

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["upstream", "ui"]
    assert result.stderr == ""


def test_sync_stale_bare_cache_clones_remote_created_branch(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    warm = tmp_path / "warm"
    runner.invoke(app, ["init", "warm", "--path", str(warm)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "warm"])
    warmed = runner.invoke(app, ["sync", "--workspace", "warm"])
    assert warmed.exit_code == 0, warmed.output

    seed = tmp_path / "_branch_seed"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "tag.gpgsign", "false"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-b", "develop"],
        check=True,
        capture_output=True,
    )
    (seed / "develop.txt").write_text("develop")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "develop"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "develop"],
        check=True,
        capture_output=True,
    )

    target = tmp_path / "target"
    runner.invoke(app, ["init", "target", "--path", str(target)])
    runner.invoke(
        app,
        ["add", f"file://{upstream}", "--workspace", "target", "--branch", "develop"],
    )
    synced = runner.invoke(app, ["sync", "--workspace", "target"])

    assert synced.exit_code == 0, synced.output
    head = subprocess.run(
        ["git", "-C", str(target / "upstream"), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "develop"


def test_sync_parallel_warns_when_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing ``-j`` above the cap is honoured (clamped) but a stderr
    warning surfaces the truncation so users notice when they ask for
    more concurrency than they get.

    The cap follows ``2 * os.cpu_count()`` (shared with ``foreach``);
    we pin ``cpu_count`` so the assertion isn't CI-hardware dependent.
    """
    monkeypatch.setattr("os.cpu_count", lambda: 4)
    runner = CliInvoker()
    # No targets registered → the pool runs over an empty list, which is
    # fine for asserting the warning fires before the sweep starts.
    result = runner.invoke(app, ["sync", "--all", "-j", "100"])
    assert result.exit_code == 0, result.output
    assert "clamped to 8" in result.output
    assert "2 * os.cpu_count()" in result.output


def test_status_after_sync(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(
        app,
        [
            "status",
            "--workspace",
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


def test_status_repo_filter_outputs_only_selected_repo(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    runner.invoke(app, ["add", "https://x/ui.git", "--repo-name", "ui", "--workspace", "prod"])

    result = runner.invoke(
        app,
        [
            "status",
            "--workspace",
            "prod",
            "--repo",
            "api",
            "--format",
            "raw",
            "--columns",
            "repo",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["api"]


def test_status_honors_global_ui_collection_view_for_table_output(
    isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text(
        "name: prod\nrepos:\n  - url: https://x/api.git\n    name: api\n"
    )
    isolate_config.write_text(
        f"""
        profiles:
          default:
            ui:
              collection_view: list
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )

    result = CliInvoker().invoke(
        app,
        [
            "status",
            "--workspace",
            "prod",
            "--format",
            "table",
            "--columns",
            "workspace",
            "--columns",
            "repo",
            "--columns",
            "cloned",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "workspace: prod" in result.stdout
    assert "repo: api" in result.stdout
    assert "cloned: False" in result.stdout
    assert "╭" not in result.stdout
    assert "┌" not in result.stdout


def test_status_all_malformed_registry_entry_stays_hard_error(isolate_config: Path) -> None:
    isolate_config.write_text(
        "workspace:\n  workspaces:\n    - name: prod\n",
        encoding="utf-8",
    )

    result = CliInvoker().invoke(app, ["status", "--all"])

    assert result.exit_code != 0
    assert "invalid workspace registry entry 'prod': missing or empty 'path'" in result.output


def _patch_shell_runner(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    """Route the CLI's foreach runner through ``fake``."""

    def _call(_self: object, cmd: str, cwd: Path, *, timeout: float) -> Any:
        return fake(cmd, cwd, timeout=timeout)

    monkeypatch.setattr(InterruptibleShellRunner, "__call__", _call)


def test_foreach_runs_command_in_each_repo(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(
        app, ["foreach", "git rev-parse --abbrev-ref HEAD", "--workspace", "smoke"]
    )
    assert result.exit_code == 0, result.output
    # Output is prefixed `[upstream] main`
    assert "[upstream] main" in result.stdout


def test_foreach_repo_filter_runs_command_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _runner(cmd: str, cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(cwd.name)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    _patch_shell_runner(monkeypatch, _runner)
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    runner.invoke(app, ["add", "https://x/ui.git", "--repo-name", "ui", "--workspace", "prod"])
    (target / "api").mkdir()
    (target / "ui").mkdir()

    result = runner.invoke(
        app,
        ["foreach", "echo ok", "--workspace", "prod", "--repo", "api", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["api"]
    assert json.loads(result.stdout)[0]["repo"] == "api"


def test_foreach_unknown_repo_filter_exits_before_running_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _runner(cmd: str, cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(cwd.name)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    _patch_shell_runner(monkeypatch, _runner)
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    (target / "api").mkdir()

    result = runner.invoke(app, ["foreach", "echo ok", "--workspace", "prod", "--repo", "ghost"])

    assert result.exit_code == 1
    assert "1 unknown repo identifier for --repo: ghost" in result.stderr
    assert calls == []


def test_foreach_structured_format(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    """`--format json` emits ForeachOutcome rows; the [repo]-prefixed
    passthrough is suppressed so downstream tools can parse stdout."""
    import json as _json

    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(
        app,
        [
            "foreach",
            "git rev-parse --abbrev-ref HEAD",
            "--workspace",
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


def test_foreach_timeout_zero_is_rejected(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    result = runner.invoke(app, ["foreach", "true", "--workspace", "prod", "--timeout", "0"])

    assert result.exit_code != 0
    assert "--timeout must be positive" in result.output


def test_foreach_summary_suppressed_in_structured_format(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """Machine formats stay clean — failures are conveyed by `returncode`
    on each row, not by the human summary line."""
    import json as _json

    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(
        app,
        ["foreach", "false", "--workspace", "smoke", "--ignore-errors", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed
    assert any(row["returncode"] != 0 for row in parsed)
    assert "failed in:" not in (result.stderr or "")


def test_sync_json_stdout_shape_stays_data_only(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])

    result = runner.invoke(app, ["sync", "--workspace", "smoke", "--format", "json"])

    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.stdout)
    # ``workspace`` leads so ``--format raw`` defaults to it.
    assert list(row) == ["workspace", "repo", "action", "detail", "target_path"]
    assert row["action"] == "cloned"
    assert (target / "upstream").is_dir()


@pytest.mark.parametrize("args", [["sync"], ["status"], ["foreach"], ["branch", "apply"]])
def test_repo_operating_commands_expose_repo_filter(args: list[str]) -> None:
    result = CliInvoker().invoke(app, [*args, "--help"])

    assert result.exit_code == 0, result.output
    assert "--repo" in result.output
    assert "-r" in result.output
    assert "--only" not in result.output


@pytest.mark.parametrize("command", ["sync", "status"])
@pytest.mark.parametrize("target", [["--workspace", "smoke"], ["--path", "."]])
def test_all_rejects_explicit_target(command: str, target: list[str]) -> None:
    result = CliInvoker().invoke(app, [command, "--all", *target])
    assert result.exit_code == 2
    assert "--all cannot be combined with --workspace or --path" in result.stderr


def test_sync_all_parallel_covers_every_workspace_in_registry_order(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """``sync --all -j 4`` syncs every workspace, names the worker count on
    stderr, and emits rows in registry order (not completion order)."""
    runner = CliInvoker()
    names = ("alpha", "beta", "gamma", "delta")
    for name in names:
        runner.invoke(app, ["init", name, "--path", str(tmp_path / f"ws-{name}")])
        runner.invoke(app, ["add", f"file://{upstream}", "--workspace", name])

    result = runner.invoke(
        app,
        ["sync", "--all", "-j", "4", "--format", "raw", "--columns", "workspace", "-c", "action"],
    )

    assert result.exit_code == 0, result.output
    assert "syncing 4 repos with up to 4 workers" in result.stderr
    assert "sync: 4 cloned" in result.stderr
    assert result.stdout.splitlines() == [f"{n}\tcloned" for n in names]


def _register_alpha_and_ghost(isolate_config: Path, tmp_path: Path, url: str) -> Path:
    """Register ``alpha`` (one repo at ``url``) and ``ghost`` (no manifest)."""
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    (alpha / "untaped.yml").write_text(
        f"name: alpha\nrepos:\n  - url: {url}\n    name: upstream\n", encoding="utf-8"
    )
    isolate_config.write_text(
        "workspace:\n  workspaces:\n"
        f"    - name: alpha\n      path: {alpha}\n"
        f"    - name: ghost\n      path: {tmp_path / 'ghost'}\n",
        encoding="utf-8",
    )
    return alpha


def test_sync_all_unavailable_manifest_does_not_abort_valid_workspace(
    tmp_path: Path, upstream: Path, isolate_config: Path, isolated_cache: Path
) -> None:
    alpha = _register_alpha_and_ghost(isolate_config, tmp_path, f"file://{upstream}")

    result = CliInvoker().invoke(app, ["sync", "--all", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(r["workspace"], r["repo"], r["action"]) for r in rows] == [
        ("alpha", "upstream", "cloned"),
        ("ghost", "", "unavailable"),
    ]
    assert rows[1]["detail"].startswith("workspace manifest unavailable: no manifest at")
    assert (alpha / "upstream").is_dir()


def test_status_all_unavailable_manifest_outputs_machine_visible_row(
    tmp_path: Path, isolate_config: Path
) -> None:
    _register_alpha_and_ghost(isolate_config, tmp_path, "https://x/api.git")

    result = CliInvoker().invoke(app, ["status", "--all", "--format", "json"])

    assert result.exit_code == 0, result.output
    alpha, ghost = json.loads(result.stdout)
    assert (alpha["workspace"], alpha["action"]) == ("alpha", "status")
    assert {k: v for k, v in ghost.items() if k not in {"detail", "target_path"}} == {
        "workspace": "ghost",
        "repo": "",
        "action": "unavailable",
        "cloned": False,
        "branch": None,
        "ahead": 0,
        "behind": 0,
        "modified": 0,
        "untracked": 0,
    }
    assert ghost["detail"].startswith(
        f"workspace manifest unavailable: no manifest at {tmp_path / 'ghost'}/untaped.yml"
    )


def test_status_all_unreadable_manifest_does_not_abort_valid_workspace(
    tmp_path: Path, isolate_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_alpha_and_ghost(isolate_config, tmp_path, "https://x/api.git")
    ghost_manifest = tmp_path / "ghost" / "untaped.yml"
    ghost_manifest.parent.mkdir()
    ghost_manifest.write_text("name: ghost\n", encoding="utf-8")
    original = Path.read_text

    def _read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == ghost_manifest:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    result = CliInvoker().invoke(
        app,
        ["status", "--all", "--format", "raw", "-c", "workspace", "-c", "action", "-c", "detail"],
    )

    assert result.exit_code == 0, result.output
    alpha, ghost = result.stdout.splitlines()
    assert alpha == "alpha\tstatus\t"
    assert ghost.startswith("ghost\tunavailable\t")
    assert f"could not read manifest at {ghost_manifest}" in ghost


def _workspace_with_api_dir(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "prod", "--path", str(tmp_path / "ws")])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    (tmp_path / "ws" / "api").mkdir()


@pytest.mark.parametrize(("extra", "expected"), [([], 600.0), (["--timeout", "12.5"], 12.5)])
def test_foreach_timeout_wires_to_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str], expected: float
) -> None:
    seen: list[float] = []

    def _runner(cmd: str, cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append(timeout)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    _patch_shell_runner(monkeypatch, _runner)
    _workspace_with_api_dir(tmp_path)

    result = CliInvoker().invoke(app, ["foreach", "echo ok", "--workspace", "prod", *extra])

    assert result.exit_code == 0, result.output
    assert seen == [expected]


def test_foreach_timeout_json_output(tmp_path: Path) -> None:
    _workspace_with_api_dir(tmp_path)
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(60)')}"

    result = CliInvoker().invoke(
        app, ["foreach", command, "--workspace", "prod", "--timeout", "0.1", "--format", "json"]
    )

    assert result.exit_code == 1
    (row,) = json.loads(result.stdout)
    assert (row["repo"], row["returncode"]) == ("api", 124)
    assert "timed out after 0.1s" in row["stderr"]


@pytest.mark.parametrize(
    ("flags", "exit_code"),
    [([], 1), (["--continue-on-error"], 1), (["--ignore-errors"], 0)],
)
def test_foreach_failure_summary_and_exit_code(
    tmp_path: Path, upstream: Path, isolated_cache: Path, flags: list[str], exit_code: int
) -> None:
    """Failures always surface in the summary line; only ``--ignore-errors``
    exits 0 (``--continue-on-error`` keeps going but still exits 1)."""
    runner = CliInvoker()
    runner.invoke(app, ["init", "smoke", "--path", str(tmp_path / "ws")])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--workspace", "smoke", *flags])

    assert result.exit_code == exit_code, result.output
    assert "failed in: upstream" in result.stderr


def test_foreach_parallel_zero_is_a_usage_error() -> None:
    result = CliInvoker().invoke(app, ["foreach", "true", "--workspace", "smoke", "-j", "0"])
    assert result.exit_code == 2, result.output
    assert "Must be >= 1" in result.stderr


@pytest.mark.parametrize(
    ("command", "hints"),
    [
        (["sync"], ["Syncing repos", "sync: nothing to do", "Nothing to sync"]),
        (["status"], ["No cloned repos"]),
        (["foreach", "true"], ["No repos matched"]),
    ],
)
def test_empty_workspace_guides_with_stderr_hint(
    tmp_path: Path, command: list[str], hints: list[str]
) -> None:
    """A repo-less workspace keeps stdout pipe-clean and guides on stderr."""
    runner = CliInvoker()
    runner.invoke(app, ["init", "solo", "--path", str(tmp_path / "solo")])

    result = runner.invoke(app, [*command, "--workspace", "solo"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert [h for h in hints if h not in result.stderr] == []
