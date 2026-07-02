"""Shared release workflow checks for untaped packages."""

from __future__ import annotations

import argparse
import ast
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

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9][A-Za-z0-9._+-]*)?")
TESTPYPI_INDEX = "https://test.pypi.org/simple/"


class ReleaseCheckError(RuntimeError):
    """A release precondition failed."""


def verify_version(version: str, *, pyproject_path: Path = PYPROJECT) -> None:
    """Verify the requested release version is safe and matches project metadata."""
    if VERSION_RE.fullmatch(version) is None:
        raise ReleaseCheckError(f"unsafe or invalid release version input: {version!r}")

    project = _project_metadata(pyproject_path)
    actual = str(project["version"])
    if actual != version:
        raise ReleaseCheckError(
            f"workflow input version {version!r} does not match pyproject.toml {actual!r}"
        )
    print(f"ok: package metadata version matches workflow input {version}")


def verify_target_unused(version: str) -> None:
    """Fail closed if the production GitHub release or tag already exists."""
    check_github_release_absent(version)
    check_git_tag_absent(version)


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
    """Smoke a package install by checking metadata, console script, and help."""
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


def _dependency_name(requirement: str) -> str:
    match = re.match(r"([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        return ""
    return _normalize_package_name(match.group(1))


def _normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _project_metadata(pyproject_path: Path) -> dict[str, Any]:
    text = pyproject_path.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)["project"]
    return _parse_project_metadata(text)


def _parse_project_metadata(text: str) -> dict[str, Any]:
    """Parse the project metadata subset needed by pre-sync release checks."""
    project_lines: list[str] = []
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("[") and stripped.endswith("]"):
            break
        if in_project:
            project_lines.append(line)

    project: dict[str, Any] = {}
    index = 0
    while index < len(project_lines):
        stripped = project_lines[index].strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            index += 1
            continue

        key, raw_value = [part.strip() for part in stripped.split("=", maxsplit=1)]
        if key in {"name", "version"}:
            project[key] = ast.literal_eval(raw_value)
        elif key == "dependencies":
            value_lines = [raw_value]
            while "]" not in value_lines[-1]:
                index += 1
                value_lines.append(project_lines[index].strip())
            project[key] = ast.literal_eval("\n".join(value_lines))
        index += 1

    return project


def main(argv: list[str] | None = None) -> int:
    """Run release checks from the command line."""
    parser = argparse.ArgumentParser(description="Run release workflow checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_version_parser = subparsers.add_parser("verify-version")
    verify_version_parser.add_argument("version")
    verify_version_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    verify_target_parser = subparsers.add_parser("verify-target-unused")
    verify_target_parser.add_argument("version")

    verify_internal_deps_parser = subparsers.add_parser("verify-internal-dependencies-published")
    verify_internal_deps_parser.add_argument("--index", choices=["testpypi", "pypi"], required=True)
    verify_internal_deps_parser.add_argument("--pyproject", type=Path, default=PYPROJECT)

    smoke_console_parser = subparsers.add_parser("smoke-console")
    smoke_console_parser.add_argument("--package", required=True)
    smoke_console_parser.add_argument("--version", required=True)
    smoke_console_parser.add_argument("--venv", type=Path, required=True)
    smoke_console_parser.add_argument("--console-script", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "verify-version":
            verify_version(args.version, pyproject_path=args.pyproject)
        elif args.command == "verify-target-unused":
            verify_target_unused(args.version)
        elif args.command == "verify-internal-dependencies-published":
            verify_internal_dependencies_published(args.index, pyproject_path=args.pyproject)
        elif args.command == "smoke-console":
            smoke_console(
                package_name=args.package,
                version=args.version,
                python_path=args.venv / "bin" / "python",
                console_script=args.venv / "bin" / args.console_script,
            )
        else:  # pragma: no cover - argparse enforces choices.
            parser.error(f"unknown command: {args.command}")
    except ReleaseCheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
