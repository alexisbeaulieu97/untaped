"""Unit tests for shared release workflow helper logic."""

from __future__ import annotations

import importlib.util
import subprocess
import urllib.error
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / ".github" / "release" / "release.py"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("untaped_release_helper", HELPER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pyproject(
    tmp_path: Path,
    *,
    name: str = "untaped-github",
    version: str = "0.12.5",
    dependencies: list[str] | None = None,
) -> Path:
    dependencies = dependencies or ["untaped>=2.4.4,<3"]
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "\n".join(
            [
                "[project]",
                f'name = "{name}"',
                f'version = "{version}"',
                "dependencies = [",
                *[f'    "{dependency}",' for dependency in dependencies],
                "]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class _Response:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self) -> bytes:
        return b""


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/alexisbeaulieu97/untaped-github/releases/tags/v0.12.5",
        code=code,
        msg="status",
        hdrs=None,
        fp=None,
    )


def test_default_paths_point_at_repo_root() -> None:
    release = _load_helper()

    assert release.ROOT == REPO_ROOT
    assert release.PYPROJECT == REPO_ROOT / "pyproject.toml"


def test_main_verify_version_accepts_pyproject_option(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release = _load_helper()
    pyproject = _pyproject(tmp_path, version="9.8.7")

    assert release.main(["verify-version", "9.8.7", "--pyproject", str(pyproject)]) == 0
    assert "matches workflow input 9.8.7" in capsys.readouterr().out


def test_main_verify_internal_dependencies_accepts_pyproject_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _load_helper()
    pyproject = _pyproject(tmp_path)
    calls: list[tuple[str, Path]] = []

    def verify(index: str, *, pyproject_path: Path) -> None:
        calls.append((index, pyproject_path))

    monkeypatch.setattr(release, "verify_internal_dependencies_published", verify)

    assert (
        release.main(
            [
                "verify-internal-dependencies-published",
                "--index",
                "testpypi",
                "--pyproject",
                str(pyproject),
            ]
        )
        == 0
    )
    assert calls == [("testpypi", pyproject)]


def test_verify_version_matches_pyproject_and_rejects_unsafe_input(tmp_path: Path) -> None:
    release = _load_helper()
    pyproject = _pyproject(tmp_path)

    release.verify_version("0.12.5", pyproject_path=pyproject)

    with pytest.raises(release.ReleaseCheckError, match="does not match"):
        release.verify_version("0.12.6", pyproject_path=pyproject)
    with pytest.raises(release.ReleaseCheckError, match="unsafe or invalid"):
        release.verify_version("0.12.5; echo injected", pyproject_path=pyproject)


def test_project_metadata_fallback_works_without_tomllib(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _load_helper()
    pyproject = _pyproject(
        tmp_path,
        name="untaped-ansible",
        dependencies=["untaped>=2.4.4,<3", "untaped-github>=0.12.5,<0.13"],
    )
    monkeypatch.setattr(release, "tomllib", None)

    release.verify_version("0.12.5", pyproject_path=pyproject)
    assert release.internal_dependency_requirements(pyproject) == [
        "untaped>=2.4.4,<3",
        "untaped-github>=0.12.5,<0.13",
    ]


def test_github_release_check_fails_when_release_exists() -> None:
    release = _load_helper()

    with pytest.raises(release.ReleaseCheckError, match="already exists"):
        release.check_github_release_absent(
            "0.12.5",
            repo="alexisbeaulieu97/untaped-github",
            token="token",
            urlopen=lambda _request, timeout: _Response(200),
        )


def test_github_release_check_accepts_404_as_absent() -> None:
    release = _load_helper()

    def raise_not_found(_request: object, timeout: int) -> object:
        raise _http_error(404)

    release.check_github_release_absent(
        "0.12.5",
        repo="alexisbeaulieu97/untaped-github",
        token="token",
        urlopen=raise_not_found,
    )


def test_github_release_check_fails_closed_on_unexpected_http_status() -> None:
    release = _load_helper()

    def raise_forbidden(_request: object, timeout: int) -> object:
        raise _http_error(403)

    with pytest.raises(release.ReleaseCheckError, match="could not verify"):
        release.check_github_release_absent(
            "0.12.5",
            repo="alexisbeaulieu97/untaped-github",
            token="token",
            urlopen=raise_forbidden,
        )


def test_github_release_check_fails_closed_on_network_error() -> None:
    release = _load_helper()

    def raise_network_error(_request: object, timeout: int) -> object:
        raise urllib.error.URLError("dns failed")

    with pytest.raises(release.ReleaseCheckError, match="could not verify"):
        release.check_github_release_absent(
            "0.12.5",
            repo="alexisbeaulieu97/untaped-github",
            token="token",
            urlopen=raise_network_error,
        )


def test_git_tag_check_maps_exit_codes_fail_closed() -> None:
    release = _load_helper()
    calls: list[list[str]] = []

    def runner(returncode: int) -> Any:
        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, returncode, "", "stderr")

        return run

    with pytest.raises(release.ReleaseCheckError, match="already exists"):
        release.check_git_tag_absent("0.12.5", runner=runner(0))

    release.check_git_tag_absent("0.12.5", runner=runner(2))

    with pytest.raises(release.ReleaseCheckError, match="could not verify"):
        release.check_git_tag_absent("0.12.5", runner=runner(128))

    assert calls[0] == [
        "git",
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        "refs/tags/v0.12.5",
    ]


def test_internal_dependencies_are_read_from_pyproject_and_exclude_self(
    tmp_path: Path,
) -> None:
    release = _load_helper()
    pyproject = _pyproject(
        tmp_path,
        name="untaped-ansible",
        dependencies=[
            "cyclopts>=4.16.0,<5",
            "untaped>=2.4.4,<3",
            "untaped-github>=0.12.5,<0.13",
            "untaped-ansible>=0.11.1",
        ],
    )

    assert release.internal_dependency_requirements(pyproject) == [
        "untaped>=2.4.4,<3",
        "untaped-github>=0.12.5,<0.13",
    ]


def test_internal_dependency_matching_normalizes_names(tmp_path: Path) -> None:
    release = _load_helper()
    pyproject = _pyproject(
        tmp_path,
        name="untaped.github",
        dependencies=[
            "Untaped>=2.4.4,<3",
            "untaped_github>=0.12.5",
            "untaped-workspace>=0.10.1",
        ],
    )

    assert release.internal_dependency_requirements(pyproject) == [
        "Untaped>=2.4.4,<3",
        "untaped-workspace>=0.10.1",
    ]


def test_verify_internal_dependencies_published_uses_testpypi_index_strategy(
    tmp_path: Path,
) -> None:
    release = _load_helper()
    pyproject = _pyproject(
        tmp_path,
        name="untaped-ansible",
        dependencies=["untaped>=2.4.4,<3", "untaped-github>=0.12.5,<0.13"],
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0, "2.4.4", "")

    release.verify_internal_dependencies_published(
        "testpypi", pyproject_path=pyproject, runner=runner
    )

    assert len(calls) == 2
    assert calls[0][0][:5] == ["uv", "run", "--no-project", "--refresh-package", "untaped"]
    assert "untaped>=2.4.4,<3" in calls[0][0]
    assert calls[1][0][:5] == [
        "uv",
        "run",
        "--no-project",
        "--refresh-package",
        "untaped-github",
    ]
    assert "untaped-github>=0.12.5,<0.13" in calls[1][0]
    for _command, env in calls:
        assert env["UV_INDEX"] == "https://test.pypi.org/simple/"
        assert env["UV_INDEX_STRATEGY"] == "unsafe-best-match"


def test_smoke_console_checks_version_script_and_help(
    tmp_path: Path,
) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped-github"
    console_script.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.chmod(0o755)
    calls: list[list[str]] = []

    def runner(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "0.12.5", "")

    release.smoke_console(
        package_name="untaped-github",
        version="0.12.5",
        python_path=python_path,
        console_script=console_script,
        runner=runner,
    )

    assert calls[0][0] == str(python_path)
    assert "metadata.version('untaped-github')" in " ".join(calls[0])
    assert calls[1] == [str(console_script), "--help"]


def test_smoke_console_fails_when_console_script_missing(tmp_path: Path) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(release.ReleaseCheckError, match="expected console script"):
        release.smoke_console(
            package_name="untaped-github",
            version="0.12.5",
            python_path=python_path,
            console_script=tmp_path / "venv" / "bin" / "untaped-github",
        )
