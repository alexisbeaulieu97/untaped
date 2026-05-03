import subprocess
from pathlib import Path

from untaped_workspace.application import Foreach
from untaped_workspace.domain import Repo, Workspace, WorkspaceManifest
from untaped_workspace.infrastructure import ManifestRepository


def _seed(tmp_path: Path, manifest: WorkspaceManifest) -> Workspace:
    ws = tmp_path / "prod"
    ws.mkdir()
    ManifestRepository().write(ws, manifest)
    for repo in manifest.repos:
        assert repo.name is not None
        (ws / repo.name).mkdir()
    return Workspace(name="prod", path=ws)


def _runner_factory(returncode: dict[str, int] | None = None):
    returncode = returncode or {}

    def _runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
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
    outcomes = Foreach(ManifestRepository(), runner=runner)(workspace, command="echo hi")
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
    outcomes = Foreach(ManifestRepository(), runner=runner)(workspace, command="x")
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
    outcomes = Foreach(ManifestRepository(), runner=runner)(
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
    outcomes = Foreach(ManifestRepository(), runner=runner)(
        workspace, command="x", parallel=4, continue_on_error=True
    )
    assert [o.repo for o in outcomes] == ["a", "b", "c", "d"]


def test_skips_uncloned_repo(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ManifestRepository().write(ws_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    workspace = Workspace(name="prod", path=ws_path)
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner)(workspace, command="x")
    assert outcomes[0].returncode == -1
    assert "not cloned" in outcomes[0].stderr


def test_outcome_records_command_and_duration(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git")]),
    )
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner)(workspace, command="echo hi")
    outcome = outcomes[0]
    assert outcome.command == "echo hi"
    assert outcome.duration_s >= 0.0


def test_outcome_records_command_when_uncloned(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ManifestRepository().write(ws_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    workspace = Workspace(name="prod", path=ws_path)
    runner = _runner_factory()
    outcomes = Foreach(ManifestRepository(), runner=runner)(workspace, command="echo hi")
    assert outcomes[0].command == "echo hi"
    assert outcomes[0].duration_s == 0.0
