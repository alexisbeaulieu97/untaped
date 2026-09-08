"""Shared release workflow checks for untaped packages."""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_RELEASE_DIR = Path(__file__).resolve().parent
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))

import _release_core  # noqa: E402
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
    "verify_index_artifacts",
]


def smoke_unified_app(
    *,
    package_name: str,
    version: str,
    python_path: Path,
    console_script: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Run the single installed-app smoke used by local and published jobs."""
    _smoke_installed_package(
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


def _smoke_installed_package(
    *,
    package_name: str,
    version: str,
    python_path: Path,
    console_script: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Smoke an installed package by checking metadata, version output, and help."""
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


def verify_version(version: str, *, pyproject_path: Path = PYPROJECT) -> None:
    """Verify the requested release version against project metadata."""
    _release_core.verify_version(version, pyproject_path=pyproject_path)


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
    state = "draft" if release.draft else "published"
    print(f"ok: GitHub {state} {release.tag} contains the exact candidate assets")


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


if __name__ == "__main__":
    raise SystemExit(main())
