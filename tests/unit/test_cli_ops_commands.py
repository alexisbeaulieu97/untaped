"""CLI tests for workspace sync, status, and foreach commands."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_sync_clones_repos(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])

    result = runner.invoke(
        app,
        [
            "sync",
            "--workspace",
            "smoke",
            "--format",
            "raw",
            "--columns",
            "repo",
            "--columns",
            "action",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "clone" in result.stdout
    assert (target / "upstream").is_dir()


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
    assert result.stdout.splitlines() == ["upstream\tclone"]
    assert (target / "upstream").is_dir()
    assert not (target / "ui").exists()


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
    assert "alpha\tupstream\tclone" in rows, rows
    assert "beta\tupstream\tunmatched" in rows, rows


def test_sync_help_exposes_repo_filter_not_only() -> None:
    result = CliInvoker().invoke(app, ["sync", "--help"])

    assert result.exit_code == 0, result.output
    assert "--repo" in result.output
    assert "-r" in result.output
    assert "--only" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["status"],
        ["foreach"],
        ["branch", "apply"],
    ],
)
def test_repo_operating_commands_expose_repo_filter(args: list[str]) -> None:
    result = CliInvoker().invoke(app, [*args, "--help"])

    assert result.exit_code == 0, result.output
    assert "--repo" in result.output
    assert "-r" in result.output
    assert "--only" not in result.output


def test_sync_parallel_without_all_is_rejected() -> None:
    """``--parallel >1`` only makes sense with ``--all``. Anything else is a
    usage error exit. Checked before any registry lookup so a
    nonexistent workspace name doesn't change the error."""
    runner = CliInvoker()
    result = runner.invoke(app, ["sync", "--workspace", "nope", "-j", "4"])
    assert result.exit_code != 0
    combined = (result.stderr or "") + result.output
    assert "--all" in combined


def test_sync_all_rejects_workspace_target() -> None:
    runner = CliInvoker()
    result = runner.invoke(app, ["sync", "--all", "--workspace", "smoke"])
    assert result.exit_code != 0
    assert "--all cannot be combined with --workspace or --path" in result.output


def test_sync_all_rejects_path_target(tmp_path: Path) -> None:
    runner = CliInvoker()
    result = runner.invoke(app, ["sync", "--all", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "--all cannot be combined with --workspace or --path" in result.output


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


def test_sync_all_parallel_covers_every_workspace(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """``sync --all -j 4`` syncs every registered workspace and emits a
    stderr header that names the worker count."""
    runner = CliInvoker()
    names = ("alpha", "beta", "gamma", "delta")
    for name in names:
        ws_path = tmp_path / f"ws-{name}"
        runner.invoke(app, ["init", name, "--path", str(ws_path)])
        runner.invoke(app, ["add", f"file://{upstream}", "--workspace", name])

    result = runner.invoke(
        app,
        [
            "sync",
            "--all",
            "-j",
            "4",
            "--format",
            "raw",
            "--columns",
            "workspace",
            "--columns",
            "action",
        ],
    )
    assert result.exit_code == 0, result.output
    # stderr header — CliInvoker combines stderr into output by default.
    assert "syncing 4 workspaces with up to 4 workers" in result.output
    rows = [r for r in result.stdout.strip().splitlines() if "\t" in r]
    assert sorted(rows) == sorted(f"{n}\tclone" for n in names), rows


def test_sync_all_parallel_ordering_is_stable(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """Outcome rows from ``sync --all -j 4`` come back in registry-input
    order, not in non-deterministic ``as_completed`` order. Running the
    same command twice yields identical row sequences."""
    runner = CliInvoker()
    names = ("alpha", "beta", "gamma", "delta")
    for name in names:
        ws_path = tmp_path / f"ws-{name}"
        runner.invoke(app, ["init", name, "--path", str(ws_path)])
        runner.invoke(app, ["add", f"file://{upstream}", "--workspace", name])

    def workspace_rows() -> list[str]:
        result = runner.invoke(
            app,
            ["sync", "--all", "-j", "4", "--format", "raw", "--columns", "workspace"],
        )
        assert result.exit_code == 0, result.output
        return [r for r in result.stdout.strip().splitlines() if r]

    first = workspace_rows()
    second = workspace_rows()
    assert first == second
    assert first == list(names)


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


def test_status_all_rejects_workspace_target() -> None:
    runner = CliInvoker()
    result = runner.invoke(app, ["status", "--all", "--workspace", "smoke"])
    assert result.exit_code != 0
    assert "--all cannot be combined with --workspace or --path" in result.output


def test_status_all_rejects_path_target(tmp_path: Path) -> None:
    runner = CliInvoker()
    result = runner.invoke(app, ["status", "--all", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "--all cannot be combined with --workspace or --path" in result.output


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

    def _runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cwd.name)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("untaped_workspace.cli.ops_commands.shell_runner", _runner)
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

    def _runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cwd.name)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("untaped_workspace.cli.ops_commands.shell_runner", _runner)
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    (target / "api").mkdir()

    result = runner.invoke(app, ["foreach", "echo ok", "--workspace", "prod", "--repo", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output
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


def test_foreach_format_raw_columns(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    """`--format raw --columns repo,returncode` produces tab-separated rows."""
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
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--workspace", "smoke"])
    assert result.exit_code == 1
    assert "failed in: upstream" in (result.stderr or result.output)


def test_foreach_continue_on_error_still_exits_one(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """`--continue-on-error` keeps going but still exits 1 on failures
    (pins the historical contract)."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--workspace", "smoke", "--continue-on-error"])
    assert result.exit_code == 1
    assert "failed in: upstream" in (result.stderr or result.output)


def test_foreach_ignore_errors_exits_zero_with_summary(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """`--ignore-errors` keeps going AND exits 0; failures surface via the
    summary line so they aren't silent."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(app, ["foreach", "false", "--workspace", "smoke", "--ignore-errors"])
    assert result.exit_code == 0, result.output
    assert "failed in: upstream" in (result.stderr or result.output)


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


# ── foreach --parallel: silent <1 coercion (foreach-specific UX) ─────────────
# The cap-clamp policy is unit-tested at ``clamp_parallel`` in
# ``tests/unit/test_cli_helpers.py``; the sync CLI
# test above exercises the wire-through end-to-end. Foreach has one
# divergent contract: ``-j 0`` silently coerces to serial (sync and
# ``awx apply`` raise ``BadParameter`` instead), so that's the only
# foreach-specific case worth a CLI-level pin.


def test_foreach_parallel_zero_coerces_to_serial(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    """``foreach -j 0`` runs cleanly with exit 0 and no warning — the
    ``max(parallel, 1)`` upstream of ``clamp_parallel`` keeps the use
    case from seeing ``0`` and matches the issue spec."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(app, ["foreach", "true", "--workspace", "smoke", "-j", "0"])
    assert result.exit_code == 0, result.output


def test_sync_empty_workspace_reports_progress_and_hint(tmp_path: Path) -> None:
    """A repo-less workspace still announces progress on stderr and guides with
    an empty-state hint, while keeping stdout pipe-clean."""
    runner = CliInvoker()
    runner.invoke(app, ["init", "solo", "--path", str(tmp_path / "solo")])

    result = runner.invoke(app, ["sync", "--workspace", "solo"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "Syncing workspaces" in result.stderr
    assert "Nothing to sync" in result.stderr


def test_status_empty_workspace_guides_with_stderr_hint(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "solo", "--path", str(tmp_path / "solo")])

    result = runner.invoke(app, ["status", "--workspace", "solo"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No cloned repos" in result.stderr


def test_foreach_no_matching_repos_guides_with_stderr_hint(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "solo", "--path", str(tmp_path / "solo")])

    result = runner.invoke(app, ["foreach", "true", "--workspace", "solo"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No repos matched" in result.stderr
