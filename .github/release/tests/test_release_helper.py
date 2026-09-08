"""Unit tests for shared release workflow helper logic."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / ".github" / "release" / "release.py"
PROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
PROJECT_VERSION = str(PROJECT["version"])
SYNTHETIC_VERSION = "9.8.7"


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
    name: str = "untaped",
    version: str = SYNTHETIC_VERSION,
    dependencies: list[str] | None = None,
) -> Path:
    dependency_lines = (
        ["dependencies = [", *[f'    "{dependency}",' for dependency in dependencies], "]"]
        if dependencies
        else []
    )
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "\n".join(
            [
                "[project]",
                f'name = "{name}"',
                f'version = "{version}"',
                *dependency_lines,
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


class _JsonResponse(_Response):
    def __init__(self, payload: object) -> None:
        super().__init__()
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _github_release_payload(
    *,
    release_id: int = 1,
    tag: str = f"v{SYNTHETIC_VERSION}",
    target_oid: str = "a" * 40,
    draft: bool = True,
) -> dict[str, Any]:
    return {
        "id": release_id,
        "tag_name": tag,
        "target_commitish": target_oid,
        "draft": draft,
    }


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=f"https://api.github.com/repos/acme/untaped/releases/tags/v{SYNTHETIC_VERSION}",
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
    pyproject = _pyproject(tmp_path)

    assert release.main(["verify-version", SYNTHETIC_VERSION, "--pyproject", str(pyproject)]) == 0
    assert f"matches workflow input {SYNTHETIC_VERSION}" in capsys.readouterr().out


def test_verify_version_matches_pyproject_and_rejects_unsafe_input(tmp_path: Path) -> None:
    release = _load_helper()
    pyproject = _pyproject(tmp_path)

    release.verify_version(SYNTHETIC_VERSION, pyproject_path=pyproject)

    with pytest.raises(release.ReleaseCheckError, match="does not match"):
        release.verify_version("9.8.8", pyproject_path=pyproject)
    with pytest.raises(release.ReleaseCheckError, match="unsafe or invalid"):
        release.verify_version(f"{SYNTHETIC_VERSION}; echo injected", pyproject_path=pyproject)


@pytest.mark.parametrize(
    "version",
    [
        "9.8.7post1.dev1",
        "9.8.7.dev1",
        "9.8.7+local",
        "9.8.7-alpha1",
        "9.8.7rc",
    ],
)
def test_verify_version_rejects_unsupported_release_forms(tmp_path: Path, version: str) -> None:
    release = _load_helper()
    pyproject = _pyproject(tmp_path, version=version)

    with pytest.raises(release.ReleaseCheckError, match="unsafe or invalid"):
        release.verify_version(version, pyproject_path=pyproject)


def test_installed_package_smoke_checks_version_script_and_help(
    tmp_path: Path,
) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped"
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
        return subprocess.CompletedProcess(command, 0, f"{SYNTHETIC_VERSION}\n", "")

    release._smoke_installed_package(
        package_name="untaped",
        version=SYNTHETIC_VERSION,
        python_path=python_path,
        console_script=console_script,
        runner=runner,
    )

    assert calls[0][0] == str(python_path)
    assert "metadata.version('untaped')" in " ".join(calls[0])
    assert calls[1] == [str(console_script), "--version"]
    assert calls[2] == [str(console_script), "--help"]


def test_installed_package_smoke_fails_when_version_command_fails(tmp_path: Path) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped"
    console_script.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.chmod(0o755)

    def runner(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        if command == [str(console_script), "--version"]:
            return subprocess.CompletedProcess(command, 2, "", "version exploded")
        return subprocess.CompletedProcess(command, 0, f"{SYNTHETIC_VERSION}\n", "")

    with pytest.raises(
        release.ReleaseCheckError,
        match=r"untaped --version failed: version exploded",
    ):
        release._smoke_installed_package(
            package_name="untaped",
            version=SYNTHETIC_VERSION,
            python_path=python_path,
            console_script=console_script,
            runner=runner,
        )


@pytest.mark.parametrize(
    "stdout",
    [
        SYNTHETIC_VERSION,
        "9.8.8\n",
        f"{SYNTHETIC_VERSION} extra\n",
        f"{SYNTHETIC_VERSION}\n\n",
        f"untaped {SYNTHETIC_VERSION}\n",
    ],
)
def test_installed_package_smoke_rejects_wrong_or_extra_version_stdout(
    tmp_path: Path,
    stdout: str,
) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped"
    console_script.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.chmod(0o755)

    def runner(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        output = (
            stdout if command == [str(console_script), "--version"] else f"{SYNTHETIC_VERSION}\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    with pytest.raises(
        release.ReleaseCheckError,
        match=r"untaped --version output .* did not match '9\.8\.7\\n'",
    ):
        release._smoke_installed_package(
            package_name="untaped",
            version=SYNTHETIC_VERSION,
            python_path=python_path,
            console_script=console_script,
            runner=runner,
        )


def test_installed_package_smoke_fails_when_console_script_missing(tmp_path: Path) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(release.ReleaseCheckError, match="expected console script"):
        release._smoke_installed_package(
            package_name="untaped",
            version=SYNTHETIC_VERSION,
            python_path=python_path,
            console_script=tmp_path / "venv" / "bin" / "untaped",
        )


def test_smoke_unified_checks_exact_capability_metadata(tmp_path: Path) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped"
    console_script.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.write_text("#!/bin/sh\n", encoding="utf-8")
    console_script.chmod(0o755)
    root_commands = (
        "config profile skills doctor capabilities workspace github jira awx ansible recipe "
        "orchestration"
    )
    rows = [
        {
            "name": command,
            "origin": "built-in",
            "status": "ready",
            "distribution": "untaped",
            "version": PROJECT_VERSION,
            "api": ">=1.0,<2.0",
        }
        for command in release.BUILTIN_CAPABILITIES
    ]

    def runner(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        metadata_command = (
            "import importlib.metadata as metadata; print(metadata.version('untaped'))"
        )
        if command == [str(python_path), "-c", metadata_command]:
            return subprocess.CompletedProcess(command, 0, f"{PROJECT_VERSION}\n", "")
        if command == [str(console_script), "--version"]:
            return subprocess.CompletedProcess(command, 0, f"{PROJECT_VERSION}\n", "")
        if command == [str(console_script), "--help"]:
            return subprocess.CompletedProcess(command, 0, root_commands, "")
        if command == [str(console_script), "capabilities", "--format", "json"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    release.smoke_unified_app(
        package_name="untaped",
        version=PROJECT_VERSION,
        python_path=python_path,
        console_script=console_script,
        runner=runner,
    )


class _FakePublicationTransport:
    def __init__(self, release: Any = None, index_files: dict[str, str] | None = None) -> None:
        self.release = release
        self.assets: dict[str, str] = dict(release.assets) if release is not None else {}
        self.index_files = index_files
        self.calls: list[str] = []
        self.fail_after: str | None = None
        self.tag_target: str | None = None
        self.tag_missing = False
        self.missing_after_publish = False

    def _fail(self, operation: str) -> None:
        if self.fail_after == operation:
            self.fail_after = None
            raise release_module.ReleaseCheckError(f"injected {operation} failure")

    def inspect_github_release(self, *, tag: str) -> Any:
        self.calls.append(f"inspect-release:{tag}")
        return self.release

    def inspect_tag_target(self, *, tag: str) -> str | None:
        if self.tag_missing:
            return None
        if self.tag_target is not None:
            return self.tag_target
        return None if self.release is None else self.release.target_oid

    def create_github_draft(self, candidate: Any) -> Any:
        self.calls.append("create-draft")
        self.release = release_module.GitHubRelease(
            release_id="1",
            tag=candidate.tag,
            target_oid=candidate.candidate_oid,
            draft=True,
            assets={},
        )
        self._fail("create-draft")
        return self.release

    def inspect_github_assets(self, release: Any) -> dict[str, str]:
        self.calls.append("inspect-assets")
        return dict(self.assets)

    def upload_github_asset(self, release: Any, artifact: Any) -> None:
        self.calls.append(f"upload-asset:{artifact.filename}")
        self.assets[artifact.filename] = artifact.sha256
        self._fail("upload-asset")

    def inspect_index(self, *, index: str, candidate: Any) -> dict[str, str] | None:
        self.calls.append(f"inspect-index:{index}")
        return None if self.index_files is None else dict(self.index_files)

    def upload_index(self, *, index: str, candidate: Any) -> None:
        self.calls.append(f"upload-index:{index}")
        self.index_files = candidate.artifact_hashes
        self._fail("upload-index")

    def smoke_published(self, *, index: str, candidate: Any) -> None:
        self.calls.append(f"smoke:{index}")
        self._fail("smoke")

    def publish_github_release(self, release: Any) -> Any:
        self.calls.append("publish-release")
        self.release = release_module.GitHubRelease(
            release_id=release.release_id,
            tag=release.tag,
            target_oid=release.target_oid,
            draft=False,
            assets=dict(self.assets),
        )
        if self.missing_after_publish:
            self.tag_missing = True
        self._fail("publish-release")
        return self.release


def _candidate(tmp_path: Path) -> Any:
    wheel = tmp_path / f"untaped-{SYNTHETIC_VERSION}-py3-none-any.whl"
    sdist = tmp_path / f"untaped-{SYNTHETIC_VERSION}.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    artifact_type = release_module.ReleaseArtifact
    artifacts = tuple(
        artifact_type(path.name, __import__("hashlib").sha256(path.read_bytes()).hexdigest(), path)
        for path in (wheel, sdist)
    )
    return release_module.ReleaseCandidate("untaped", SYNTHETIC_VERSION, "a" * 40, artifacts)


release_module: ModuleType = _load_helper()


def test_manifest_matches_package_and_lock() -> None:
    release_module.validate_release_manifest(lock_path=REPO_ROOT / "uv.lock")


def test_release_cli_executes_when_invoked_as_a_script() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "verify-version",
            PROJECT_VERSION,
            "--pyproject",
            str(REPO_ROOT / "pyproject.toml"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"ok: package metadata version matches workflow input {PROJECT_VERSION}" in result.stdout


def test_manifest_rejects_mutated_core_source_provenance(tmp_path: Path) -> None:
    text = (REPO_ROOT / "release-manifest.toml").read_text(encoding="utf-8")
    altered = text.replace(
        'oid = "2283bfc51ea2cdcd4195e76eec4f3fa985479ced"',
        f'oid = "{"b" * 40}"',
        1,
    )
    path = tmp_path / "release-manifest.toml"
    path.write_text(altered, encoding="utf-8")
    with pytest.raises(release_module.ReleaseCheckError, match="core source oid"):
        release_module.validate_release_manifest(path, lock_path=REPO_ROOT / "uv.lock")


def test_manifest_rejects_broadened_source_intersection(tmp_path: Path) -> None:
    text = (REPO_ROOT / "release-manifest.toml").read_text(encoding="utf-8")
    altered = text.replace('filelock = ">=3.29.7,<4"', 'filelock = ">=3.29.0,<4"', 1)
    path = tmp_path / "release-manifest.toml"
    path.write_text(altered, encoding="utf-8")
    with pytest.raises(release_module.ReleaseCheckError, match="source dependency intersections"):
        release_module.validate_release_manifest(path, lock_path=REPO_ROOT / "uv.lock")


@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        (
            'status = "integrated-reviewed"',
            'status = "approved-not-integrated"',
            "integrated-reviewed",
        ),
        (
            '"d68e6a9007d11f50ce9e4c0a21296322975ec0ca"',
            f'"{"b" * 40}"',
            "source OIDs are not approved",
        ),
    ],
)
def test_manifest_rejects_unapproved_orchestration_provenance(
    tmp_path: Path, needle: str, replacement: str, message: str
) -> None:
    text = (REPO_ROOT / "release-manifest.toml").read_text(encoding="utf-8")
    altered = text.replace(needle, replacement, 1)
    path = tmp_path / "release-manifest.toml"
    path.write_text(altered, encoding="utf-8")
    with pytest.raises(release_module.ReleaseCheckError, match=message):
        release_module.validate_release_manifest(path, lock_path=REPO_ROOT / "uv.lock")


@pytest.mark.parametrize(
    ("needle", "message"),
    [
        ("capabilities = [", "capability order"),
        (f'version = "{PROJECT_VERSION}"', "version"),
        ('requires-python = ">=3.14"', "Python floor"),
    ],
)
def test_manifest_rejects_stale_public_identity(tmp_path: Path, needle: str, message: str) -> None:
    text = (REPO_ROOT / "release-manifest.toml").read_text(encoding="utf-8")
    if needle == "capabilities = [":
        altered = re.sub(
            r"^capabilities = .*?$",
            'capabilities = ["workspace"]',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        replacement = None
    elif needle == 'requires-python = ">=3.14"':
        replacement = 'requires-python = ">=3.13"'
    else:
        replacement = f'version = "{SYNTHETIC_VERSION}"'
    if replacement is not None:
        altered = text.replace(needle, replacement, 1)
    path = tmp_path / "release-manifest.toml"
    path.write_text(altered, encoding="utf-8")
    with pytest.raises(release_module.ReleaseCheckError, match=message):
        release_module.validate_release_manifest(path, lock_path=REPO_ROOT / "uv.lock")


def test_release_candidate_requires_exact_commit_and_wheel_sdist_set(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"untaped-{SYNTHETIC_VERSION}-py3-none-any.whl").write_bytes(b"wheel")
    (dist / f"untaped-{SYNTHETIC_VERSION}.tar.gz").write_bytes(b"sdist")
    pyproject = _pyproject(tmp_path, version=SYNTHETIC_VERSION)
    candidate = release_module.collect_release_candidate(
        version=SYNTHETIC_VERSION,
        candidate_oid="a" * 40,
        current_oid="a" * 40,
        dist_dir=dist,
        pyproject_path=pyproject,
    )
    assert candidate.artifact_hashes == {
        f"untaped-{SYNTHETIC_VERSION}-py3-none-any.whl": __import__("hashlib")
        .sha256(b"wheel")
        .hexdigest(),
        f"untaped-{SYNTHETIC_VERSION}.tar.gz": __import__("hashlib").sha256(b"sdist").hexdigest(),
    }
    with pytest.raises(release_module.ReleaseCheckError, match="does not match reviewed"):
        release_module.collect_release_candidate(
            version=SYNTHETIC_VERSION,
            candidate_oid="b" * 40,
            current_oid="a" * 40,
            dist_dir=dist,
            pyproject_path=pyproject,
        )


@pytest.mark.parametrize(
    "filenames",
    [
        (
            f"untaped-{SYNTHETIC_VERSION}-py3-none-any.whl",
            f"untaped-{SYNTHETIC_VERSION}.tar.gz",
            "notes.txt",
        ),
        (
            f"other-{SYNTHETIC_VERSION}-py3-none-any.whl",
            f"untaped-{SYNTHETIC_VERSION}.tar.gz",
        ),
        (
            f"untaped-{SYNTHETIC_VERSION}0-py3-none-any.whl",
            f"untaped-{SYNTHETIC_VERSION}.tar.gz",
        ),
        (
            f"untaped-{SYNTHETIC_VERSION}-py3-none-any.whl",
            f"untaped-{SYNTHETIC_VERSION}-py3.14-none-any.whl",
            f"untaped-{SYNTHETIC_VERSION}.tar.gz",
        ),
        (
            f"untaped-{SYNTHETIC_VERSION}-py3-none-any.whl",
            f"untaped-{SYNTHETIC_VERSION}.tar",
        ),
    ],
    ids=[
        "extra-file",
        "wrong-distribution",
        "suffix-adjacent-version",
        "duplicate-wheel",
        "malformed-archive",
    ],
)
def test_release_candidate_rejects_non_exact_distribution_sets(
    tmp_path: Path, filenames: tuple[str, ...]
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    for filename in filenames:
        (dist / filename).write_bytes(filename.encode())

    with pytest.raises(release_module.ReleaseCheckError):
        release_module.collect_release_candidate(
            version=SYNTHETIC_VERSION,
            candidate_oid="a" * 40,
            current_oid="a" * 40,
            dist_dir=dist,
            pyproject_path=REPO_ROOT / "pyproject.toml",
        )


def test_publication_fresh_run_follows_draft_index_smoke_publish(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()

    assert (
        release_module.run_publication(candidate, index="pypi", transport=transport)
        == release_module.PublicationState.GITHUB_PUBLISHED
    )
    assert transport.calls.count("create-draft") == 1
    assert transport.calls.count("upload-index:pypi") == 1
    assert transport.calls.index("smoke:pypi") < transport.calls.index("publish-release")
    assert transport.calls[-1] == "publish-release"


def test_publication_resume_skips_exact_draft_assets_and_index_upload(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=True,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)

    release_module.run_publication(candidate, index="pypi", transport=transport)

    assert "create-draft" not in transport.calls
    assert not any(call.startswith("upload-asset:") for call in transport.calls)
    assert "upload-index:pypi" not in transport.calls
    assert transport.calls.index("smoke:pypi") < transport.calls.index("publish-release")
    assert transport.calls[-1] == "publish-release"


def test_publication_complete_matching_release_is_verified_noop(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=False,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)

    assert (
        release_module.run_publication(candidate, index="pypi", transport=transport)
        == release_module.PublicationState.GITHUB_PUBLISHED
    )
    assert "publish-release" not in transport.calls
    assert "smoke:pypi" in transport.calls
    assert transport.calls[-1] == "inspect-index:pypi"


def test_production_draft_helper_resumes_exact_published_prefix_as_noop(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=False,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)

    result = release_module.ensure_github_draft(candidate, transport=transport)

    assert result == release
    assert "create-draft" not in transport.calls
    assert not any(call.startswith("upload-asset:") for call in transport.calls)


def test_production_draft_helper_allows_uncreated_tag_until_publish(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=True,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)
    transport.tag_missing = True

    assert release_module.ensure_github_draft(candidate, transport=transport) == release


@pytest.mark.parametrize("operation", ["ensure", "publish"])
def test_production_helpers_reject_missing_tag_for_published_release(
    tmp_path: Path, operation: str
) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=False,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)
    transport.tag_missing = True

    with pytest.raises(release_module.ReleaseCheckError, match="resolves to None"):
        if operation == "ensure":
            release_module.ensure_github_draft(candidate, transport=transport)
        else:
            release_module.publish_github_draft(candidate, transport=transport)


def test_production_publish_rechecks_tag_after_publish(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=True,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)
    transport.missing_after_publish = True

    with pytest.raises(release_module.ReleaseCheckError, match="resolves to None"):
        release_module.publish_github_draft(candidate, transport=transport)


def test_production_publish_helper_uses_exact_shared_prefix_and_noops_when_complete(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    release = release_module.GitHubRelease(
        release_id="1",
        tag=candidate.tag,
        target_oid=candidate.candidate_oid,
        draft=False,
        assets=candidate.artifact_hashes,
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)

    result = release_module.publish_github_draft(candidate, transport=transport)

    assert result == release
    assert "publish-release" not in transport.calls
    assert transport.calls.count("inspect-index:pypi") == 1


@pytest.mark.parametrize("operation", ["ensure", "publish"])
@pytest.mark.parametrize("conflict", ["target", "tag", "asset", "extra"])
def test_production_release_transitions_share_fail_closed_conflict_matrix(
    tmp_path: Path, operation: str, conflict: str
) -> None:
    candidate = _candidate(tmp_path)
    target = "b" * 40 if conflict == "target" else candidate.candidate_oid
    assets = dict(candidate.artifact_hashes)
    if conflict == "asset":
        assets[next(iter(assets))] = "0" * 64
    elif conflict == "extra":
        assets["unexpected.txt"] = "0" * 64
    release = release_module.GitHubRelease(
        release_id="1", tag=candidate.tag, target_oid=target, draft=True, assets=assets
    )
    transport = _FakePublicationTransport(release, candidate.artifact_hashes)
    if conflict == "tag":
        transport.tag_target = "b" * 40
    with pytest.raises(release_module.ReleaseCheckError):
        if operation == "ensure":
            release_module.ensure_github_draft(candidate, transport=transport)
        elif operation == "publish":
            release_module.publish_github_draft(candidate, transport=transport)

    assert "publish-release" not in transport.calls
    assert not any(call.startswith("upload-asset:") for call in transport.calls)


@pytest.mark.parametrize("operation", ["index", "prepare"])
@pytest.mark.parametrize("conflict", ["index", "extra"])
def test_production_index_transitions_share_fail_closed_conflict_matrix(
    tmp_path: Path, operation: str, conflict: str
) -> None:
    candidate = _candidate(tmp_path)
    index = dict(candidate.artifact_hashes)
    if conflict == "index":
        index[next(iter(index))] = "0" * 64
    else:
        index["unexpected.txt"] = "0" * 64
    transport = _FakePublicationTransport(index_files=index)
    output_dir = tmp_path / "upload"

    with pytest.raises(release_module.ReleaseCheckError):
        if operation == "index":
            release_module.verify_index_artifacts(candidate, index="pypi", transport=transport)
        else:
            release_module.prepare_index_upload(
                candidate,
                index="pypi",
                transport=transport,
                output_dir=output_dir,
            )


def test_publication_injected_upload_failure_resumes_without_duplicate(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()
    transport.fail_after = "upload-index"
    with pytest.raises(release_module.ReleaseCheckError, match="injected upload-index"):
        release_module.run_publication(candidate, index="pypi", transport=transport)

    before = transport.calls.count("upload-index:pypi")
    release_module.run_publication(candidate, index="pypi", transport=transport)
    assert before == 1
    assert transport.calls.count("upload-index:pypi") == 1


def test_production_create_failure_after_response_resumes_existing_draft(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()
    transport.fail_after = "create-draft"

    with pytest.raises(release_module.ReleaseCheckError, match="injected create-draft"):
        release_module.ensure_github_draft(candidate, transport=transport)

    release_module.ensure_github_draft(candidate, transport=transport)
    assert transport.calls.count("create-draft") == 1
    assert transport.calls.count("upload-asset:" + candidate.artifacts[0].filename) == 1
    assert transport.calls.count("upload-asset:" + candidate.artifacts[1].filename) == 1


def test_production_partial_asset_upload_resumes_missing_suffix(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()
    transport.fail_after = "upload-asset"

    with pytest.raises(release_module.ReleaseCheckError, match="injected upload-asset"):
        release_module.ensure_github_draft(candidate, transport=transport)

    release_module.ensure_github_draft(candidate, transport=transport)
    assert transport.calls.count("create-draft") == 1
    assert transport.calls.count("upload-asset:" + candidate.artifacts[0].filename) == 1
    assert transport.calls.count("upload-asset:" + candidate.artifacts[1].filename) == 1


def test_publication_smoke_failure_resumes_without_reupload(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()
    transport.fail_after = "smoke"

    with pytest.raises(release_module.ReleaseCheckError, match="injected smoke"):
        release_module.run_publication(candidate, index="pypi", transport=transport)

    assert (
        release_module.run_publication(candidate, index="pypi", transport=transport)
        == release_module.PublicationState.GITHUB_PUBLISHED
    )
    assert transport.calls.count("create-draft") == 1
    assert transport.calls.count("upload-index:pypi") == 1
    assert transport.calls.count("publish-release") == 1


def test_publication_failure_after_publish_response_resumes_as_noop(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()
    transport.fail_after = "publish-release"

    with pytest.raises(release_module.ReleaseCheckError, match="injected publish-release"):
        release_module.run_publication(candidate, index="pypi", transport=transport)

    assert (
        release_module.run_publication(candidate, index="pypi", transport=transport)
        == release_module.PublicationState.GITHUB_PUBLISHED
    )
    assert transport.calls.count("publish-release") == 1


@pytest.mark.parametrize("conflict", ["target", "tag", "asset", "extra", "index"])
def test_publication_conflicts_fail_closed_before_next_mutation(
    tmp_path: Path, conflict: str
) -> None:
    candidate = _candidate(tmp_path)
    target = "b" * 40 if conflict == "target" else candidate.candidate_oid
    assets = dict(candidate.artifact_hashes)
    index = dict(candidate.artifact_hashes)
    if conflict == "asset":
        assets[next(iter(assets))] = "0" * 64
    elif conflict == "extra":
        assets["unexpected.txt"] = "0" * 64
    elif conflict == "index":
        index[next(iter(index))] = "0" * 64
    release = release_module.GitHubRelease(
        release_id="1", tag=candidate.tag, target_oid=target, draft=True, assets=assets
    )
    transport = _FakePublicationTransport(release, index if conflict == "index" else None)
    if conflict == "tag":
        transport.tag_target = "b" * 40
    with pytest.raises(release_module.ReleaseCheckError):
        release_module.run_publication(candidate, index="pypi", transport=transport)
    assert "upload-index:pypi" not in transport.calls
    assert "publish-release" not in transport.calls


def test_testpypi_resume_omits_github_draft_leg(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    transport = _FakePublicationTransport()

    assert (
        release_module.run_publication(candidate, index="testpypi", transport=transport)
        == release_module.PublicationState.PUBLISHED_SMOKE
    )
    assert not any(
        call.endswith("-draft") or call.startswith("upload-asset") for call in transport.calls
    )
    assert "upload-index:testpypi" in transport.calls


def test_simple_index_rejects_unexpected_same_version_file(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    hashes = candidate.artifact_hashes
    html = "".join(
        f'<a href="{name}#sha256={digest}">{name}</a>' for name, digest in hashes.items()
    )
    html += f'<a href="untaped-{SYNTHETIC_VERSION}-extra.whl#sha256=' + "0" * 64 + '">extra</a>'

    class HtmlResponse(_Response):
        def read(self) -> bytes:
            return html.encode("utf-8")

    transport = release_module.SimpleIndexTransport(
        urlopen=lambda _request, timeout: HtmlResponse()
    )
    with pytest.raises(release_module.ReleaseCheckError, match="unexpected files"):
        release_module.verify_index_artifacts(candidate, index="pypi", transport=transport)


def test_prepare_index_upload_copies_only_missing_artifacts(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    existing = {
        next(iter(candidate.artifact_hashes)): next(iter(candidate.artifact_hashes.values()))
    }
    transport = _FakePublicationTransport(index_files=existing)
    upload_dir = tmp_path / "upload"

    assert not release_module.prepare_index_upload(
        candidate,
        index="pypi",
        transport=transport,
        output_dir=upload_dir,
    )
    assert [path.name for path in upload_dir.iterdir()] == [f"untaped-{SYNTHETIC_VERSION}.tar.gz"]


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [
        ("4.0.0", False),
        ("4.0.0a1", True),
        ("4.0.0b1", True),
        ("4.0.0rc1", True),
    ],
)
def test_github_transport_draft_payload_marks_prereleases(
    version: str,
    prerelease: bool,
) -> None:
    candidate = release_module.ReleaseCandidate("untaped", version, "a" * 40, ())
    requests: list[Any] = []

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        requests.append(request)
        return _JsonResponse(
            _github_release_payload(tag=f"v{version}", target_oid=candidate.candidate_oid)
        )

    transport = release_module.GitHubReleaseTransport(
        repo="acme/untaped", token="token", urlopen=urlopen
    )
    transport.create_github_draft(candidate)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.full_url == "https://api.github.com/repos/acme/untaped/releases"
    assert request.data is not None
    assert json.loads(request.data) == {
        "tag_name": f"v{version}",
        "target_commitish": candidate.candidate_oid,
        "name": f"untaped v{version}",
        "body": f"PyPI release for untaped {version}.",
        "draft": True,
        "prerelease": prerelease,
        "generate_release_notes": False,
    }


@pytest.mark.parametrize(
    "version",
    [
        "4.0.0post1.dev1",
        "4.0.0.dev1",
        "4.0.0+local",
        "4.0.0-alpha1",
        "4.0.0rc",
    ],
)
def test_github_transport_rejects_unsupported_release_forms(version: str) -> None:
    candidate = release_module.ReleaseCandidate("untaped", version, "a" * 40, ())
    requests: list[Any] = []

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        requests.append(request)
        return _JsonResponse(
            _github_release_payload(tag=f"v{version}", target_oid=candidate.candidate_oid)
        )

    transport = release_module.GitHubReleaseTransport(
        repo="acme/untaped", token="token", urlopen=urlopen
    )
    with pytest.raises(release_module.ReleaseCheckError, match="unsafe or invalid"):
        transport.create_github_draft(candidate)
    assert requests == []


def test_github_transport_peels_annotated_tag_to_commit() -> None:
    release = release_module.GitHubReleaseTransport
    tag_object = "b" * 40
    commit_object = "a" * 40

    class JsonResponse(_Response):
        def __init__(self, payload: dict[str, Any]) -> None:
            super().__init__()
            self.payload = payload

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def urlopen(request: Any, timeout: int) -> JsonResponse:
        if request.full_url.endswith(f"/git/ref/tags/v{SYNTHETIC_VERSION}"):
            return JsonResponse({"object": {"type": "tag", "sha": tag_object}})
        assert request.full_url.endswith(f"/git/tags/{tag_object}")
        return JsonResponse({"object": {"type": "commit", "sha": commit_object}})

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    assert transport.inspect_tag_target(tag=f"v{SYNTHETIC_VERSION}") == commit_object


def test_github_transport_finds_draft_when_tag_route_hides_drafts() -> None:
    release = release_module.GitHubReleaseTransport
    tag = f"v{SYNTHETIC_VERSION}"
    target_oid = "b" * 40
    calls: list[str] = []

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        calls.append(request.full_url)
        if "/releases/tags/" in request.full_url:
            raise _http_error(404)
        assert request.full_url.endswith("/releases?per_page=100&page=1")
        return _JsonResponse(
            [_github_release_payload(release_id=17, tag=tag, target_oid=target_oid)]
        )

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    found = transport.inspect_github_release(tag=tag)

    assert found == release_module.GitHubRelease(
        release_id="17", tag=tag, target_oid=target_oid, draft=True, assets={}
    )
    assert calls == [
        f"https://api.github.com/repos/acme/untaped/releases/tags/v{SYNTHETIC_VERSION}",
        "https://api.github.com/repos/acme/untaped/releases?per_page=100&page=1",
    ]


def test_github_transport_paginates_release_list_after_tag_route_404() -> None:
    release = release_module.GitHubReleaseTransport
    tag = f"v{SYNTHETIC_VERSION}"
    first_page = [
        _github_release_payload(release_id=index + 1, tag=f"v-other-{index}")
        for index in range(100)
    ]
    second_page = [_github_release_payload(release_id=101, tag=tag)]
    pages = {1: first_page, 2: second_page}
    requested_pages: list[int] = []

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        if "/releases/tags/" in request.full_url:
            raise _http_error(404)
        page = int(request.full_url.rsplit("page=", 1)[1])
        requested_pages.append(page)
        return _JsonResponse(pages[page])

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    found = transport.inspect_github_release(tag=tag)

    assert found is not None
    assert found.release_id == "101"
    assert found.tag == tag
    assert requested_pages == [1, 2]


def test_github_transport_rejects_ambiguous_matching_releases() -> None:
    release = release_module.GitHubReleaseTransport
    tag = f"v{SYNTHETIC_VERSION}"

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        if "/releases/tags/" in request.full_url:
            raise _http_error(404)
        return _JsonResponse(
            [
                _github_release_payload(release_id=1, tag=tag),
                _github_release_payload(release_id=2, tag=tag),
            ]
        )

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    with pytest.raises(release_module.ReleaseCheckError, match="multiple releases"):
        transport.inspect_github_release(tag=tag)


def test_github_transport_rejects_malformed_matching_release() -> None:
    release = release_module.GitHubReleaseTransport
    tag = f"v{SYNTHETIC_VERSION}"

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        if "/releases/tags/" in request.full_url:
            raise _http_error(404)
        return _JsonResponse([{"id": 1, "tag_name": tag, "target_commitish": "a" * 40}])

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    with pytest.raises(release_module.ReleaseCheckError, match="malformed release"):
        transport.inspect_github_release(tag=tag)


def test_github_transport_preserves_tag_route_api_errors() -> None:
    release = release_module.GitHubReleaseTransport
    calls: list[str] = []

    def urlopen(request: Any, timeout: int) -> _Response:
        del timeout
        calls.append(request.full_url)
        raise _http_error(403)

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    with pytest.raises(release_module.ReleaseCheckError, match="HTTP 403"):
        transport.inspect_github_release(tag=f"v{SYNTHETIC_VERSION}")
    assert len(calls) == 1


def test_github_transport_rejects_tag_route_mismatch() -> None:
    release = release_module.GitHubReleaseTransport
    requested_tag = f"v{SYNTHETIC_VERSION}"

    def urlopen(request: Any, timeout: int) -> _Response:
        del request, timeout
        return _JsonResponse(_github_release_payload(tag="v9.8.8"))

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    with pytest.raises(release_module.ReleaseCheckError, match="unexpected tag"):
        transport.inspect_github_release(tag=requested_tag)
