"""`workspace run` end to end with real repos."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("workspace_env")]
run = CliInvoker().invoke
SCRIPT = 'echo "$UNTAPED_BRANCH"\n'


@pytest.fixture
def ws(make_upstream: Callable[..., Path], workspace_env: Path) -> Path:
    result = run(
        app,
        [
            "create",
            "J-1",
            "--repo",
            str(make_upstream("api")),
            "--read-only",
            str(make_upstream("docs")),
        ],
    )
    assert result.exit_code == 0, result.output
    return workspace_env / "J-1"


def _rows(result) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    return json.loads(result.stdout)


def test_command_string_with_a_pipe(ws: Path) -> None:
    result = run(app, ["run", "J-1", "git log --oneline | wc -l", "--format", "json"])
    assert result.exit_code == 0, result.output
    [row] = _rows(result)  # read-only docs is excluded by default
    assert (row["repo"], row["stdout"].strip()) == ("acme/api", "1")


def test_include_read_only(ws: Path) -> None:
    rows = _rows(run(app, ["run", "J-1", "true", "--include-read-only", "--format", "json"]))
    assert sorted(r["dir"] for r in rows) == ["api", "docs"]


def test_name_from_cwd(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ws / "api")
    assert run(app, ["run", "true"]).exit_code == 0


def test_relative_script_resolves_against_the_caller(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "s.sh").write_text('echo "$UNTAPED_REPO"\n')
    monkeypatch.chdir(tmp_path)
    rows = _rows(run(app, ["run", "J-1", "s.sh", "--format", "json"]))
    assert rows[0]["stdout"].strip() == "acme/api"


def test_stdin_script(ws: Path) -> None:
    rows = _rows(
        run(app, ["run", "J-1", "-", "--format", "json"], input='echo "$UNTAPED_BRANCH"\n')
    )
    assert rows[0]["stdout"].strip() == "J-1"


def test_stdin_script_with_stdin_selection_is_usage(ws: Path) -> None:
    assert run(app, ["run", "J-1", "-", "--stdin"], input="true\n").exit_code == 2


def test_failure_exits_one_and_human_summary(ws: Path) -> None:
    result = run(app, ["run", "J-1", "exit 4", "--include-read-only"])
    assert result.exit_code == 1
    assert "── acme/api (api) ──" in result.stdout
    assert "failed" in result.stderr


def test_unknown_repo_selector(ws: Path) -> None:
    result = run(app, ["run", "J-1", "true", "--repo", "nope"])
    assert result.exit_code == 2
    assert "api" in result.output
