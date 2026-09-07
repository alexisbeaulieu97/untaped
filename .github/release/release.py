"""Shared release workflow checks for untaped packages."""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by monkeypatch.
    tomllib = None  # type: ignore[assignment]

_RELEASE_DIR = Path(__file__).resolve().parent
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))

from _publication import (  # noqa: E402
    GitHubRelease,
    PublicationState,
    PublicationTransport,
    ReleaseArtifact,
    ReleaseCandidate,
    collect_release_candidate,
    ensure_github_draft,
    prepare_index_upload,
    publish_github_draft,
    run_publication,
    validate_release_manifest,
    verify_candidate_oid,
    verify_index_artifacts,
)
from _release_core import (  # noqa: E402
    BUILTIN_CAPABILITIES,
    FULL_SHA_RE,
    MANAGEMENT_COMMANDS,
    MANIFEST,
    PYPI_INDEX,
    PYPROJECT,
    ROOT,
    SHA256_RE,
    SOURCE_EVIDENCE_PATH,
    TESTPYPI_INDEX,
    VERSION_RE,
    ReleaseCheckError,
    dependency_name,
    normalize_package_name,
    parse_project_metadata,
    project_metadata,
    requirement_specifier,
    verify_version,
)
from _release_transport import GitHubReleaseTransport, SimpleIndexTransport  # noqa: E402

__all__ = [
    "BUILTIN_CAPABILITIES",
    "FULL_SHA_RE",
    "MANAGEMENT_COMMANDS",
    "MANIFEST",
    "PYPI_INDEX",
    "PYPROJECT",
    "ROOT",
    "SHA256_RE",
    "SOURCE_EVIDENCE_PATH",
    "TESTPYPI_INDEX",
    "VERSION_RE",
    "GitHubRelease",
    "GitHubReleaseTransport",
    "PublicationState",
    "PublicationTransport",
    "ReleaseArtifact",
    "ReleaseCandidate",
    "ReleaseCheckError",
    "SimpleIndexTransport",
    "collect_release_candidate",
    "ensure_github_draft",
    "prepare_index_upload",
    "publish_github_draft",
    "run_publication",
    "validate_release_manifest",
    "verify_candidate_oid",
    "verify_index_artifacts",
]


_dependency_name = dependency_name
_normalize_package_name = normalize_package_name
_parse_project_metadata = parse_project_metadata
_requirement_specifier = requirement_specifier
_core_verify_version = verify_version


def smoke_unified_app(
    *,
    package_name: str,
    version: str,
    python_path: Path,
    console_script: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Run the single installed-app smoke used by local and published jobs."""
    smoke_console(
        package_name=package_name,
        version=version,
        python_path=python_path,
        console_script=console_script,
        runner=runner,
    )
    root_help = _run_smoke_command([str(console_script), "--help"], runner=runner)
    root_output = f"{root_help.stdout}\n{root_help.stderr}"
    for command in (*MANAGEMENT_COMMANDS, *BUILTIN_CAPABILITIES):
        if re.search(rf"(?<![a-z0-9_-]){re.escape(command)}(?![a-z0-9_-])", root_output) is None:
            raise ReleaseCheckError(f"root help is missing command {command!r}")
    capability_listing = _run_smoke_command(
        [str(console_script), "capabilities", "--format", "json"],
        runner=runner,
    )
    try:
        capability_rows = json.loads(capability_listing.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise ReleaseCheckError("capabilities metadata was not valid JSON") from error
    expected_rows = [
        {
            "name": command,
            "origin": "built-in",
            "status": "ready",
            "distribution": package_name,
            "version": version,
            "api": ">=1.0,<2.0",
        }
        for command in BUILTIN_CAPABILITIES
    ]
    if capability_rows != expected_rows:
        raise ReleaseCheckError(
            "capabilities metadata did not contain the exact seven ready built-ins"
        )
    for command in BUILTIN_CAPABILITIES:
        _run_smoke_command([str(console_script), command, "--help"], runner=runner)
    print(
        "ok: unified app smoke passed for "
        f"{package_name} {version} ({len(MANAGEMENT_COMMANDS)} management, "
        f"{len(BUILTIN_CAPABILITIES)} capabilities)"
    )


def _run_smoke_command(
    command: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> subprocess.CompletedProcess[str]:
    completed = runner(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseCheckError(f"{' '.join(command)} failed: {detail}")
    return completed


def _load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise ReleaseCheckError("TOML validation requires Python 3.11 or newer")
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseCheckError(f"could not read TOML file {path}: {error}") from error
    return parsed


def verify_target_unused(
    version: str,
    *,
    candidate_oid: str | None = None,
    current_oid: str | None = None,
) -> None:
    """Fail closed on conflicts, while allowing an exact resumable prefix."""
    if candidate_oid is None:
        check_github_release_absent(version)
        check_git_tag_absent(version)
        return
    if current_oid is None:
        raise ReleaseCheckError("current OID is required when checking an existing release prefix")
    verify_candidate_oid(candidate_oid, current_oid)
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        raise ReleaseCheckError(
            "could not verify existing release prefix: repository/token missing"
        )
    transport = GitHubReleaseTransport(repo=repo, token=token)
    release = transport.inspect_github_release(tag=f"v{version}")
    if release is None:
        check_git_tag_absent(version)
        print(f"ok: GitHub release v{version} and its tag are absent")
        return
    if release.tag != f"v{version}" or release.target_oid != candidate_oid:
        raise ReleaseCheckError(
            f"existing GitHub release v{version} does not match reviewed candidate state"
        )
    if transport.inspect_tag_target(tag=release.tag) != candidate_oid:
        raise ReleaseCheckError(
            f"existing Git tag v{version} does not resolve to the reviewed candidate"
        )
    state = "draft" if release.draft else "published"
    print(f"ok: existing GitHub {state} v{version} targets reviewed candidate")


def check_github_release_absent(
    version: str,
    *,
    repo: str | None = None,
    token: str | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Fail closed unless GitHub proves the release tag is absent."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo:
        raise ReleaseCheckError("could not verify GitHub release: GITHUB_REPOSITORY is missing")
    if not token:
        raise ReleaseCheckError("could not verify GitHub release: GH_TOKEN is missing")

    quoted_tag = urllib.parse.quote(f"v{version}", safe="")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/{quoted_tag}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            status = int(getattr(response, "status", 0))
            if status == 200:
                raise ReleaseCheckError(f"GitHub release v{version} already exists.")
            raise ReleaseCheckError(
                f"could not verify GitHub release v{version}: unexpected HTTP {status}"
            )
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return
        raise ReleaseCheckError(
            f"could not verify GitHub release v{version}: HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        raise ReleaseCheckError(f"could not verify GitHub release v{version}: {error}") from error


def check_git_tag_absent(
    version: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Fail closed unless git proves the remote tag is absent."""
    command = [
        "git",
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        f"refs/tags/v{version}",
    ]
    completed = runner(command, capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        raise ReleaseCheckError(f"Git tag v{version} already exists on origin.")
    if completed.returncode == 2:
        return

    detail = completed.stderr.strip() or completed.stdout.strip()
    suffix = f": {detail}" if detail else ""
    raise ReleaseCheckError(
        f"could not verify Git tag v{version}: git ls-remote exited {completed.returncode}{suffix}"
    )


def verify_internal_dependencies_published(
    index: str,
    *,
    pyproject_path: Path = PYPROJECT,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Verify internal untaped dependencies resolve from the selected install path."""
    requirements = internal_dependency_requirements(pyproject_path)
    if not requirements:
        print("ok: no internal untaped dependencies declared")
        return

    env = os.environ.copy()
    if index == "testpypi":
        env["UV_INDEX"] = TESTPYPI_INDEX
        env["UV_INDEX_STRATEGY"] = "unsafe-best-match"
    elif index != "pypi":
        raise ReleaseCheckError(f"unknown release index: {index}")

    for requirement in requirements:
        package_name = _dependency_name(requirement)
        _verify_dependency_published(
            package_name=package_name,
            requirement=requirement,
            index=index,
            env=env,
            runner=runner,
        )
    print(f"ok: {len(requirements)} internal dependencies resolve from {index}")


def _verify_dependency_published(
    *,
    package_name: str,
    requirement: str,
    index: str,
    env: dict[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    command = [
        "uv",
        "run",
        "--no-project",
        "--refresh-package",
        package_name,
        "--with",
        requirement,
        "python",
        "-c",
        f"import importlib.metadata as m; print(m.version({package_name!r}))",
    ]
    completed = runner(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseCheckError(f"{requirement} is not available from {index}: {detail}")


def internal_dependency_requirements(pyproject_path: Path = PYPROJECT) -> list[str]:
    """Return internal untaped dependency requirements from project metadata."""
    project = _project_metadata(pyproject_path)
    self_name = _normalize_package_name(str(project["name"]))
    requirements: list[str] = []
    for dependency in project.get("dependencies", []):
        requirement = str(dependency)
        dependency_name = _dependency_name(requirement)
        if dependency_name.startswith("untaped") and dependency_name != self_name:
            requirements.append(requirement)
    return requirements


def smoke_console(
    *,
    package_name: str,
    version: str,
    python_path: Path,
    console_script: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Smoke a package install by checking metadata, version output, and help."""
    if not python_path.exists():
        raise ReleaseCheckError(f"expected Python interpreter was missing: {python_path}")
    if not console_script.exists():
        raise ReleaseCheckError(f"expected console script was missing: {console_script}")
    if not os.access(console_script, os.X_OK):
        raise ReleaseCheckError(f"expected console script is not executable: {console_script}")

    version_check = runner(
        [
            str(python_path),
            "-c",
            (f"import importlib.metadata as metadata; print(metadata.version({package_name!r}))"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if version_check.returncode != 0:
        detail = version_check.stderr.strip() or version_check.stdout.strip()
        raise ReleaseCheckError(f"could not read installed {package_name} metadata: {detail}")

    actual = version_check.stdout.strip()
    if actual != version:
        raise ReleaseCheckError(
            f"installed {package_name} version {actual!r} did not match {version!r}"
        )

    console_version_check = runner(
        [str(console_script), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if console_version_check.returncode != 0:
        detail = console_version_check.stderr.strip() or console_version_check.stdout.strip()
        raise ReleaseCheckError(f"{console_script.name} --version failed: {detail}")

    expected_console_version = f"{version}\n"
    if console_version_check.stdout != expected_console_version:
        raise ReleaseCheckError(
            f"{console_script.name} --version output {console_version_check.stdout!r} "
            f"did not match {expected_console_version!r}"
        )

    help_check = runner(
        [str(console_script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if help_check.returncode != 0:
        detail = help_check.stderr.strip() or help_check.stdout.strip()
        raise ReleaseCheckError(f"{console_script.name} --help failed: {detail}")
    print(f"ok: {package_name} {version} console script smoke passed")


def _project_metadata(pyproject_path: Path) -> dict[str, Any]:
    """Read project metadata, retaining the legacy tomllib fallback seam."""
    return project_metadata(pyproject_path, tomllib_module=tomllib)


def verify_version(version: str, *, pyproject_path: Path = PYPROJECT) -> None:
    """Verify the requested release version against project metadata."""
    _core_verify_version(version, pyproject_path=pyproject_path, tomllib_module=tomllib)


def _collect_cli_candidate(args: argparse.Namespace) -> ReleaseCandidate:
    return collect_release_candidate(
        version=args.version,
        candidate_oid=args.candidate_oid,
        current_oid=args.current_oid,
        dist_dir=args.dist,
        pyproject_path=args.pyproject,
    )


def _handle_verify_version(args: argparse.Namespace) -> None:
    verify_version(args.version, pyproject_path=args.pyproject)


def _handle_verify_target(args: argparse.Namespace) -> None:
    verify_target_unused(
        args.version,
        candidate_oid=args.candidate_oid,
        current_oid=args.current_oid,
    )


def _handle_verify_candidate_oid(args: argparse.Namespace) -> None:
    verify_candidate_oid(args.candidate_oid, args.current_oid)


def _handle_verify_internal_dependencies(args: argparse.Namespace) -> None:
    verify_internal_dependencies_published(args.index, pyproject_path=args.pyproject)


def _handle_smoke_console(args: argparse.Namespace) -> None:
    smoke_console(
        package_name=args.package,
        version=args.version,
        python_path=args.venv / "bin" / "python",
        console_script=args.venv / "bin" / args.console_script,
    )


def _handle_smoke_unified(args: argparse.Namespace) -> None:
    smoke_unified_app(
        package_name=args.package,
        version=args.version,
        python_path=args.venv / "bin" / "python",
        console_script=args.venv / "bin" / args.console_script,
    )


def _handle_verify_candidate(args: argparse.Namespace) -> None:
    validate_release_manifest(
        args.manifest,
        pyproject_path=args.pyproject,
        lock_path=ROOT / "uv.lock",
    )
    candidate = _collect_cli_candidate(args)
    print(f"ok: candidate {candidate.candidate_oid} has {len(candidate.artifacts)} exact artifacts")


def _handle_ensure_github_draft(args: argparse.Namespace) -> None:
    candidate = _collect_cli_candidate(args)
    transport = GitHubReleaseTransport(repo=args.repo, token=args.token or "")
    release = ensure_github_draft(candidate, transport=transport)
    print(f"ok: GitHub draft {release.tag} contains the exact candidate assets")


def _handle_verify_index_artifacts(args: argparse.Namespace) -> None:
    candidate = _collect_cli_candidate(args)
    verify_index_artifacts(candidate, index=args.index, transport=SimpleIndexTransport())
    print(f"ok: {args.index} contains the exact candidate assets")


def _handle_prepare_index_upload(args: argparse.Namespace) -> None:
    candidate = _collect_cli_candidate(args)
    prepare_index_upload(
        candidate,
        index=args.index,
        transport=SimpleIndexTransport(),
        output_dir=args.output_dist,
    )


def _handle_publish_github_draft(args: argparse.Namespace) -> None:
    candidate = _collect_cli_candidate(args)
    transport = GitHubReleaseTransport(repo=args.repo, token=args.token or "")
    release = publish_github_draft(candidate, transport=transport)
    print(f"ok: GitHub release {release.tag} is published")


def _handle_verify_manifest(args: argparse.Namespace) -> None:
    validate_release_manifest(
        args.manifest,
        pyproject_path=args.pyproject,
        lock_path=args.lock,
    )
    print(f"ok: release manifest {args.manifest} matches package metadata and lockfile")


_COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {
    "verify-version": _handle_verify_version,
    "verify-target-unused": _handle_verify_target,
    "verify-candidate-oid": _handle_verify_candidate_oid,
    "verify-internal-dependencies-published": _handle_verify_internal_dependencies,
    "smoke-console": _handle_smoke_console,
    "smoke-unified": _handle_smoke_unified,
    "verify-candidate": _handle_verify_candidate,
    "ensure-github-draft": _handle_ensure_github_draft,
    "verify-index-artifacts": _handle_verify_index_artifacts,
    "prepare-index-upload": _handle_prepare_index_upload,
    "publish-github-draft": _handle_publish_github_draft,
    "verify-manifest": _handle_verify_manifest,
}


def main(argv: list[str] | None = None) -> int:
    """Run release checks from the command line."""
    parser = argparse.ArgumentParser(description="Run release workflow checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_version_parser = subparsers.add_parser("verify-version")
    verify_version_parser.add_argument("version")
    verify_version_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    verify_target_parser = subparsers.add_parser("verify-target-unused")
    verify_target_parser.add_argument("version")
    verify_target_parser.add_argument("--candidate-oid")
    verify_target_parser.add_argument("--current-oid")

    candidate_oid_parser = subparsers.add_parser("verify-candidate-oid")
    candidate_oid_parser.add_argument("--candidate-oid", required=True)
    candidate_oid_parser.add_argument("--current-oid", required=True)

    verify_internal_deps_parser = subparsers.add_parser("verify-internal-dependencies-published")
    verify_internal_deps_parser.add_argument("--index", choices=["testpypi", "pypi"], required=True)
    verify_internal_deps_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    smoke_console_parser = subparsers.add_parser("smoke-console")
    smoke_console_parser.add_argument("--package", required=True)
    smoke_console_parser.add_argument("--version", required=True)
    smoke_console_parser.add_argument("--venv", type=Path, required=True)
    smoke_console_parser.add_argument("--console-script", required=True)

    smoke_unified_parser = subparsers.add_parser("smoke-unified")
    smoke_unified_parser.add_argument("--package", required=True)
    smoke_unified_parser.add_argument("--version", required=True)
    smoke_unified_parser.add_argument("--venv", type=Path, required=True)
    smoke_unified_parser.add_argument("--console-script", required=True)

    candidate_parser = subparsers.add_parser("verify-candidate")
    candidate_parser.add_argument("--version", required=True)
    candidate_parser.add_argument("--candidate-oid", required=True)
    candidate_parser.add_argument("--current-oid", required=True)
    candidate_parser.add_argument("--dist", type=Path, required=True)
    candidate_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)
    candidate_parser.add_argument("--manifest", type=Path, default=MANIFEST)

    draft_parser = subparsers.add_parser("ensure-github-draft")
    draft_parser.add_argument("--version", required=True)
    draft_parser.add_argument("--candidate-oid", required=True)
    draft_parser.add_argument("--current-oid", required=True)
    draft_parser.add_argument("--dist", type=Path, required=True)
    draft_parser.add_argument("--repo", required=True)
    draft_parser.add_argument("--token", default=os.environ.get("GH_TOKEN"))
    draft_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    index_parser = subparsers.add_parser("verify-index-artifacts")
    index_parser.add_argument("--index", choices=["testpypi", "pypi"], required=True)
    index_parser.add_argument("--version", required=True)
    index_parser.add_argument("--candidate-oid", required=True)
    index_parser.add_argument("--current-oid", required=True)
    index_parser.add_argument("--dist", type=Path, required=True)
    index_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    prepare_index_parser = subparsers.add_parser("prepare-index-upload")
    prepare_index_parser.add_argument("--index", choices=["testpypi", "pypi"], required=True)
    prepare_index_parser.add_argument("--version", required=True)
    prepare_index_parser.add_argument("--candidate-oid", required=True)
    prepare_index_parser.add_argument("--current-oid", required=True)
    prepare_index_parser.add_argument("--dist", type=Path, required=True)
    prepare_index_parser.add_argument("--output-dist", type=Path, required=True)
    prepare_index_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    publish_parser = subparsers.add_parser("publish-github-draft")
    publish_parser.add_argument("--version", required=True)
    publish_parser.add_argument("--candidate-oid", required=True)
    publish_parser.add_argument("--current-oid", required=True)
    publish_parser.add_argument("--dist", type=Path, required=True)
    publish_parser.add_argument("--repo", required=True)
    publish_parser.add_argument("--token", default=os.environ.get("GH_TOKEN"))
    publish_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    manifest_parser = subparsers.add_parser("verify-manifest")
    manifest_parser.add_argument("--manifest", type=Path, default=MANIFEST)
    manifest_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)
    manifest_parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")

    args = parser.parse_args(argv)
    handler = _COMMAND_HANDLERS[args.command]
    try:
        handler(args)
    except ReleaseCheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0
