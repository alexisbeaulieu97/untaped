"""Unit tests for shared release workflow helper logic."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / ".github" / "release" / "release.py"
RELEASE_WORKFLOW_TEMPLATE = REPO_ROOT / ".github" / "release" / "templates" / "release.yml.tmpl"
RELEASE_TEST_TEMPLATE = (
    REPO_ROOT / ".github" / "release" / "templates" / "test_release_workflow.py.tmpl"
)
CHECKER_SHA_SENTINEL = "__CHECKER_SHA__"
OLD_CHECKER_SHA = "07116cc11d4217283ad42badea4f5d5744542f2a"


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


class _JsonResponse(_Response):
    def __init__(self, payload: object) -> None:
        super().__init__()
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _github_release_payload(
    *,
    release_id: int = 1,
    tag: str = "v4.0.0rc1",
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
        return subprocess.CompletedProcess(command, 0, "0.12.5\n", "")

    release.smoke_console(
        package_name="untaped-github",
        version="0.12.5",
        python_path=python_path,
        console_script=console_script,
        runner=runner,
    )

    assert calls[0][0] == str(python_path)
    assert "metadata.version('untaped-github')" in " ".join(calls[0])
    assert calls[1] == [str(console_script), "--version"]
    assert calls[2] == [str(console_script), "--help"]


def test_smoke_console_fails_when_version_command_fails(tmp_path: Path) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped-github"
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
        return subprocess.CompletedProcess(command, 0, "0.12.5\n", "")

    with pytest.raises(
        release.ReleaseCheckError,
        match=r"untaped-github --version failed: version exploded",
    ):
        release.smoke_console(
            package_name="untaped-github",
            version="0.12.5",
            python_path=python_path,
            console_script=console_script,
            runner=runner,
        )


@pytest.mark.parametrize(
    "stdout",
    [
        "0.12.5",
        "0.12.6\n",
        "0.12.5 extra\n",
        "0.12.5\n\n",
        "untaped-github 0.12.5\n",
    ],
)
def test_smoke_console_rejects_wrong_or_extra_version_stdout(
    tmp_path: Path,
    stdout: str,
) -> None:
    release = _load_helper()
    python_path = tmp_path / "venv" / "bin" / "python"
    console_script = tmp_path / "venv" / "bin" / "untaped-github"
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
        output = stdout if command == [str(console_script), "--version"] else "0.12.5\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    with pytest.raises(
        release.ReleaseCheckError,
        match=r"untaped-github --version output .* did not match '0.12.5\\n'",
    ):
        release.smoke_console(
            package_name="untaped-github",
            version="0.12.5",
            python_path=python_path,
            console_script=console_script,
            runner=runner,
        )


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
            "version": "4.0.0rc1",
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
            return subprocess.CompletedProcess(command, 0, "4.0.0rc1\n", "")
        if command == [str(console_script), "--version"]:
            return subprocess.CompletedProcess(command, 0, "4.0.0rc1\n", "")
        if command == [str(console_script), "--help"]:
            return subprocess.CompletedProcess(command, 0, root_commands, "")
        if command == [str(console_script), "capabilities", "--format", "json"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    release.smoke_unified_app(
        package_name="untaped",
        version="4.0.0rc1",
        python_path=python_path,
        console_script=console_script,
        runner=runner,
    )


def test_reusable_release_templates_require_checker_sha_substitution() -> None:
    workflow_template = RELEASE_WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
    test_template = RELEASE_TEST_TEMPLATE.read_text(encoding="utf-8")

    assert workflow_template.count(CHECKER_SHA_SENTINEL) == 2
    assert test_template.count(CHECKER_SHA_SENTINEL) == 1
    assert OLD_CHECKER_SHA not in workflow_template
    assert OLD_CHECKER_SHA not in test_template


def test_reusable_release_test_template_keeps_checker_sha_in_editable_block() -> None:
    template = RELEASE_TEST_TEMPLATE.read_text(encoding="utf-8")
    config_start = template.index("# ============================ PER-TOOL CONFIG")
    config_end = template.index(
        "# ========================================================================",
        config_start,
    )
    checker_assignment = template.index('CORE_RELEASE_TOOL_SHA = "__CHECKER_SHA__"')

    assert config_start < checker_assignment < config_end
    assert "Everything below the closing divider must stay byte-identical" in template


def test_reusable_release_test_template_keeps_immutable_checker_ref_contract() -> None:
    template = RELEASE_TEST_TEMPLATE.read_text(encoding="utf-8")
    module = ast.parse(template, filename=str(RELEASE_TEST_TEMPLATE))
    contract_nodes = [
        node
        for node in module.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "FULL_SHA_RE"
                for target in node.targets
            )
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "_is_immutable_core_sha")
    ]
    assert len(contract_nodes) == 2
    namespace: dict[str, object] = {"re": re}
    exec(
        compile(
            ast.Module(body=contract_nodes, type_ignores=[]),
            str(RELEASE_TEST_TEMPLATE),
            "exec",
        ),
        namespace,
    )
    validator = namespace["_is_immutable_core_sha"]
    assert callable(validator)

    invalid_refs = [
        "main",
        "v3.1.0",
        "__CHECKER_SHA__",
        "A" * 40,
        "a" * 39,
        "a" * 41,
        "a" * 40 + "\n",
    ]
    for invalid in invalid_refs:
        assert validator(invalid) is False
    assert validator("a" * 40) is True

    assert "assert _is_immutable_core_sha(CORE_RELEASE_TOOL_SHA)" in template
    assert "def test_core_release_tool_sha_validator_rejects_mutable_or_malformed_refs" in template
    immutable_assertion = template.index("assert _is_immutable_core_sha(CORE_RELEASE_TOOL_SHA)")
    checkout_comparison = template.index('assert step["with"]["ref"] == CORE_RELEASE_TOOL_SHA')
    assert immutable_assertion < checkout_comparison


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
    wheel = tmp_path / "untaped-4.0.0rc1-py3-none-any.whl"
    sdist = tmp_path / "untaped-4.0.0rc1.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    artifact_type = release_module.ReleaseArtifact
    artifacts = tuple(
        artifact_type(path.name, __import__("hashlib").sha256(path.read_bytes()).hexdigest(), path)
        for path in (wheel, sdist)
    )
    return release_module.ReleaseCandidate("untaped", "4.0.0rc1", "a" * 40, artifacts)


release_module: ModuleType = _load_helper()


def test_manifest_matches_package_and_lock() -> None:
    release_module.validate_release_manifest(lock_path=REPO_ROOT / "uv.lock")


def test_release_cli_executes_when_invoked_as_a_script() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "verify-version",
            "4.0.0rc1",
            "--pyproject",
            str(REPO_ROOT / "pyproject.toml"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok: package metadata version matches workflow input 4.0.0rc1" in result.stdout


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
        ('version = "4.0.0rc1"', "version"),
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
        replacement = needle.replace("4.0.0rc1", "4.0.0")
    if replacement is not None:
        altered = text.replace(needle, replacement, 1)
    path = tmp_path / "release-manifest.toml"
    path.write_text(altered, encoding="utf-8")
    with pytest.raises(release_module.ReleaseCheckError, match=message):
        release_module.validate_release_manifest(path, lock_path=REPO_ROOT / "uv.lock")


def test_release_candidate_requires_exact_commit_and_wheel_sdist_set(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "untaped-4.0.0rc1-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "untaped-4.0.0rc1.tar.gz").write_bytes(b"sdist")
    pyproject = REPO_ROOT / "pyproject.toml"
    candidate = release_module.collect_release_candidate(
        version="4.0.0rc1",
        candidate_oid="a" * 40,
        current_oid="a" * 40,
        dist_dir=dist,
        pyproject_path=pyproject,
    )
    assert candidate.artifact_hashes == {
        "untaped-4.0.0rc1-py3-none-any.whl": __import__("hashlib").sha256(b"wheel").hexdigest(),
        "untaped-4.0.0rc1.tar.gz": __import__("hashlib").sha256(b"sdist").hexdigest(),
    }
    with pytest.raises(release_module.ReleaseCheckError, match="does not match reviewed"):
        release_module.collect_release_candidate(
            version="4.0.0rc1",
            candidate_oid="b" * 40,
            current_oid="a" * 40,
            dist_dir=dist,
            pyproject_path=pyproject,
        )


@pytest.mark.parametrize(
    "filenames",
    [
        (
            "untaped-4.0.0rc1-py3-none-any.whl",
            "untaped-4.0.0rc1.tar.gz",
            "notes.txt",
        ),
        (
            "other-4.0.0rc1-py3-none-any.whl",
            "untaped-4.0.0rc1.tar.gz",
        ),
        (
            "untaped-4.0.0rc10-py3-none-any.whl",
            "untaped-4.0.0rc1.tar.gz",
        ),
        (
            "untaped-4.0.0rc1-py3-none-any.whl",
            "untaped-4.0.0rc1-py3.14-none-any.whl",
            "untaped-4.0.0rc1.tar.gz",
        ),
        (
            "untaped-4.0.0rc1-py3-none-any.whl",
            "untaped-4.0.0rc1.tar",
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
            version="4.0.0rc1",
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
    html += '<a href="untaped-4.0.0rc1-extra.whl#sha256=' + "0" * 64 + '">extra</a>'

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
    assert [path.name for path in upload_dir.iterdir()] == ["untaped-4.0.0rc1.tar.gz"]


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
        if request.full_url.endswith("/git/ref/tags/v4.0.0rc1"):
            return JsonResponse({"object": {"type": "tag", "sha": tag_object}})
        assert request.full_url.endswith(f"/git/tags/{tag_object}")
        return JsonResponse({"object": {"type": "commit", "sha": commit_object}})

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    assert transport.inspect_tag_target(tag="v4.0.0rc1") == commit_object


def test_github_transport_finds_draft_when_tag_route_hides_drafts() -> None:
    release = release_module.GitHubReleaseTransport
    tag = "v4.0.0rc1"
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
        "https://api.github.com/repos/acme/untaped/releases/tags/v4.0.0rc1",
        "https://api.github.com/repos/acme/untaped/releases?per_page=100&page=1",
    ]


def test_github_transport_paginates_release_list_after_tag_route_404() -> None:
    release = release_module.GitHubReleaseTransport
    tag = "v4.0.0rc1"
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
    tag = "v4.0.0rc1"

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
    tag = "v4.0.0rc1"

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
        transport.inspect_github_release(tag="v4.0.0rc1")
    assert len(calls) == 1


def test_github_transport_rejects_tag_route_mismatch() -> None:
    release = release_module.GitHubReleaseTransport
    requested_tag = "v4.0.0rc1"

    def urlopen(request: Any, timeout: int) -> _Response:
        del request, timeout
        return _JsonResponse(_github_release_payload(tag="v4.0.0rc2"))

    transport = release(repo="acme/untaped", token="token", urlopen=urlopen)
    with pytest.raises(release_module.ReleaseCheckError, match="unexpected tag"):
        transport.inspect_github_release(tag=requested_tag)
