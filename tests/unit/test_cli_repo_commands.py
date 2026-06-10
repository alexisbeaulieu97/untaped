"""CLI tests for workspace repo mutation commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_add_then_remove(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])

    a = runner.invoke(app, ["add", "https://x/svc-a.git", "--workspace", "lab"])
    assert a.exit_code == 0, a.output

    rm = runner.invoke(app, ["remove", "svc-a", "--workspace", "lab"])
    assert rm.exit_code == 0, rm.output


def test_add_unknown_workspace_errors(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["add", "https://x/a.git", "--workspace", "ghost"])
    assert result.exit_code == 1


def test_remove_accepts_multiple_repos(tmp_path: Path) -> None:
    """Repeated positional repo identifiers — drop several manifests entries
    in one call."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--workspace", "lab"])
    runner.invoke(app, ["add", "https://x/svc-b.git", "--workspace", "lab"])

    rm = runner.invoke(app, ["remove", "svc-a", "svc-b", "--workspace", "lab"])
    assert rm.exit_code == 0, rm.output


def test_remove_reads_repos_from_stdin(tmp_path: Path) -> None:
    """`workspace list ... | remove --stdin` is the documented pipeline shape."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--workspace", "lab"])
    runner.invoke(app, ["add", "https://x/svc-b.git", "--workspace", "lab"])

    rm = runner.invoke(app, ["remove", "--stdin", "--workspace", "lab"], input="svc-a\nsvc-b\n")
    assert rm.exit_code == 0, rm.output


def test_remove_continues_when_one_repo_missing(tmp_path: Path) -> None:
    """A missing repo in a multi-repo `remove` batch must not suppress
    the removals that resolved successfully — same pipeline-resilience
    rule as ``awx <kind> get --stdin`` and ``launch --stdin``."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--workspace", "lab"])

    rm = runner.invoke(app, ["remove", "ghost", "svc-a", "--workspace", "lab"])
    assert rm.exit_code != 0
    # svc-a was removed despite ghost failing — confirmed by being able to
    # re-add it without "duplicate" errors.
    re_add = runner.invoke(app, ["add", "https://x/svc-a.git", "--workspace", "lab"])
    assert re_add.exit_code == 0, re_add.output


def test_remove_prune_with_yes(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])
    assert (target / "upstream").is_dir()

    rm = runner.invoke(app, ["remove", "upstream", "--workspace", "smoke", "--prune", "--yes"])
    assert rm.exit_code == 0, rm.output
    assert not (target / "upstream").exists()


def test_remove_prune_aborts_on_no_at_prompt(
    tmp_path: Path,
    upstream: Path,
    isolated_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])
    assert (target / "upstream").is_dir()
    monkeypatch.setattr("untaped_workspace.cli.common._stdin_is_interactive", lambda: True)

    class _PromptUi:
        def confirm(self, message: str, *, default: bool = False) -> bool:
            assert "prune local clone" in message
            assert default is False
            return False

    monkeypatch.setattr("untaped_workspace.cli.common.ui_context", lambda **_: _PromptUi())

    rm = runner.invoke(
        app,
        ["remove", "upstream", "--workspace", "smoke", "--prune"],
    )

    assert rm.exit_code == 1
    assert "aborted" in rm.output
    assert (target / "upstream").is_dir()


def test_remove_prune_requires_yes_when_non_interactive(
    tmp_path: Path,
    upstream: Path,
    isolated_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])
    assert (target / "upstream").is_dir()
    monkeypatch.setattr("untaped_workspace.cli.common._stdin_is_interactive", lambda: False)

    rm = runner.invoke(app, ["remove", "upstream", "--workspace", "smoke", "--prune"])

    assert rm.exit_code == 1
    assert "--yes" in rm.output
    assert (target / "upstream").is_dir()


def test_add_accepts_multiple_positional_urls(tmp_path: Path) -> None:
    """``workspace add url-a url-b`` records both repos in one shot."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    result = runner.invoke(
        app,
        ["add", "https://x/svc-a.git", "https://x/svc-b.git", "--workspace", "lab"],
    )
    assert result.exit_code == 0, result.output
    assert "added svc-a" in (result.stderr or "")
    assert "added svc-b" in (result.stderr or "")


def test_add_reads_urls_from_stdin(tmp_path: Path) -> None:
    """``workspace list --format raw | workspace add --stdin`` is the
    documented pipeline shape."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    result = runner.invoke(
        app,
        ["add", "--stdin", "--workspace", "lab"],
        input="https://x/svc-a.git\nhttps://x/svc-b.git\n",
    )
    assert result.exit_code == 0, result.output
    assert "added svc-a" in (result.stderr or "")
    assert "added svc-b" in (result.stderr or "")


def test_add_continues_when_one_url_fails(tmp_path: Path) -> None:
    """A duplicate URL doesn't suppress the URLs that landed cleanly —
    same pipeline-resilience rule as ``workspace remove`` / ``awx get
    --stdin``."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/svc-a.git", "--workspace", "lab"])

    # svc-a is duplicate; svc-b is novel.
    result = runner.invoke(
        app,
        ["add", "https://x/svc-a.git", "https://x/svc-b.git", "--workspace", "lab"],
    )
    assert result.exit_code != 0
    # The novel URL still landed; both the success line and the per-id
    # error row land on stderr — stdout stays clean for piping.
    assert "added svc-b" in (result.stderr or "")
    assert "error: https://x/svc-a.git" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_add_rejects_mixed_positional_and_stdin(tmp_path: Path) -> None:
    """Per ``read_identifiers``: mixing positional and ``--stdin``
    is refused."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    result = runner.invoke(
        app,
        ["add", "https://x/svc-a.git", "--stdin", "--workspace", "lab"],
        input="https://x/svc-b.git\n",
    )
    assert result.exit_code != 0
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_add_repo_name_rejected_with_multiple_urls(tmp_path: Path) -> None:
    """``--repo-name`` is single-valued — applying it to a batch would
    produce a guaranteed ``DuplicateRepoName`` cascade on URL #2. The
    CLI rejects upfront with a ``BadParameter`` rather than letting the
    batch half-land."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "lab", "--path", str(target)])
    result = runner.invoke(
        app,
        [
            "add",
            "https://x/svc-a.git",
            "https://x/svc-b.git",
            "--repo-name",
            "shared",
            "--workspace",
            "lab",
        ],
    )
    assert result.exit_code != 0
    assert "--repo-name" in (result.output + (result.stderr or ""))
