import subprocess
import threading
import time
from pathlib import Path

import pytest

from untaped_workspace.application import Foreach
from untaped_workspace.domain import Repo, Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


def _seed(tmp_path: Path, manifest: WorkspaceManifest) -> Workspace:
    ws = tmp_path / "prod"
    ws.mkdir()
    ManifestRepository().write(ws, manifest)
    for repo in manifest.repos:
        assert repo.name is not None
        (ws / repo.name).mkdir()
    return Workspace(name="prod", path=ws)


def _runner_factory(
    returncode: dict[str, int] | None = None,
    raises: dict[str, Exception] | None = None,
):
    returncode = returncode or {}
    raises = raises or {}

    def _runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        if cwd.name in raises:
            raise raises[cwd.name]
        rc = returncode.get(cwd.name, 0)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=rc,
            stdout=f"ran in {cwd.name}",
            stderr="" if rc == 0 else "boom",
        )

    return _runner


def test_runs_in_each_repo_serial(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")]),
    )
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(workspace, command="echo hi")
    assert [o.repo for o in outcomes] == ["a", "b"]
    assert all(o.returncode == 0 for o in outcomes)


def test_serial_stops_on_error(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/a.git"),
                Repo(url="https://x/b.git"),
                Repo(url="https://x/c.git"),
            ]
        ),
    )
    runner = _runner_factory(returncode={"b": 2})
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(workspace, command="x")
    assert [o.repo for o in outcomes] == ["a", "b"]


def test_continue_on_error(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/a.git"),
                Repo(url="https://x/b.git"),
                Repo(url="https://x/c.git"),
            ]
        ),
    )
    runner = _runner_factory(returncode={"b": 2})
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(
        workspace, command="x", continue_on_error=True
    )
    assert [o.repo for o in outcomes] == ["a", "b", "c"]


def test_parallel_preserves_repo_order(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/a.git"),
                Repo(url="https://x/b.git"),
                Repo(url="https://x/c.git"),
                Repo(url="https://x/d.git"),
            ]
        ),
    )
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(
        workspace, command="x", parallel=4, continue_on_error=True
    )
    assert [o.repo for o in outcomes] == ["a", "b", "c", "d"]


def test_skips_uncloned_repo(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ManifestRepository().write(ws_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    workspace = Workspace(name="prod", path=ws_path)
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(workspace, command="x")
    assert outcomes[0].returncode == -1
    assert "not cloned" in outcomes[0].stderr


def test_outcome_records_command_and_duration(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git")]),
    )
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(workspace, command="echo hi")
    outcome = outcomes[0]
    assert outcome.command == "echo hi"
    assert outcome.duration_s >= 0.0


def test_outcome_records_command_when_uncloned(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ManifestRepository().write(ws_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    workspace = Workspace(name="prod", path=ws_path)
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(workspace, command="echo hi")
    assert outcomes[0].command == "echo hi"
    assert outcomes[0].duration_s == 0.0


def test_parallel_fail_fast_reports_in_flight_outcomes(tmp_path: Path) -> None:
    """Fail-fast may cancel queued futures, but in-flight commands still
    finish under ``ThreadPoolExecutor`` and must be reported."""
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/a.git"),
                Repo(url="https://x/b.git"),
                Repo(url="https://x/c.git"),
                Repo(url="https://x/d.git"),
            ]
        ),
    )
    started_b = threading.Event()
    calls: list[str] = []

    def _runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cwd.name)
        if cwd.name == "a":
            assert started_b.wait(timeout=1)
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="a", stderr="boom")
        if cwd.name == "b":
            started_b.set()
            time.sleep(0.05)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="b", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=cwd.name, stderr="")

    outcomes = Foreach(ManifestRepository(), runner=_runner, fs=_FS)(
        workspace, command="x", parallel=2
    )

    by_repo = {outcome.repo: outcome for outcome in outcomes}
    assert by_repo["a"].returncode == 1
    assert by_repo["b"].returncode == 0
    assert by_repo["b"].stdout == "b"
    assert set(by_repo).issubset(set(calls))


def test_file_not_found_yields_runner_error_outcome(tmp_path: Path) -> None:
    """A ``FileNotFoundError`` from the shell runner (e.g. shell not on PATH)
    surfaces as a ``returncode=-1`` outcome carrying the error message in
    stderr — the use case must not let the exception escape."""
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git")]),
    )
    runner = _runner_factory(raises={"a": FileNotFoundError("/bin/missing-shell: not found")})
    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(workspace, command="x")
    assert len(outcomes) == 1
    assert outcomes[0].returncode == -1
    assert outcomes[0].stdout == ""
    assert outcomes[0].stderr == "/bin/missing-shell: not found"


def test_filters_by_repo_name(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")]),
    )
    runner = _runner_factory()

    outcomes = Foreach(ManifestRepository(), runner=runner, fs=_FS)(
        workspace,
        command="echo hi",
        only=["b"],
    )

    assert [outcome.repo for outcome in outcomes] == ["b"]


def test_unknown_repo_filter_raises_before_running_command(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    calls: list[Path] = []

    def _runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cwd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with pytest.raises(WorkspaceError, match="ghost"):
        Foreach(ManifestRepository(), runner=_runner, fs=_FS)(
            workspace,
            command="echo hi",
            only=["ghost"],
        )

    assert calls == []
