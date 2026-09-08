"""Shared constants and metadata helpers for the release boundary."""

import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_RE = re.compile(
    r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)(?P<prerelease>(?:a|b|rc)[0-9]+)?$"
)
TESTPYPI_INDEX = "https://test.pypi.org/simple/"
PYPI_INDEX = "https://pypi.org/simple/"
MANIFEST = ROOT / "release-manifest.toml"
SOURCE_EVIDENCE_PATH = ROOT / "release-source-evidence.toml"
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
BUILTIN_CAPABILITIES = (
    "workspace",
    "github",
    "jira",
    "awx",
    "ansible",
    "recipe",
    "orchestration",
)
MANAGEMENT_COMMANDS = ("config", "profile", "skills", "doctor", "capabilities")


class ReleaseCheckError(RuntimeError):
    """A release precondition failed."""


def load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML document for release metadata validation."""
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseCheckError(f"could not read TOML file {path}: {error}") from error
    return parsed


def requirement_specifier(requirement: str) -> str:
    """Return the specifier portion of a PEP 508 requirement."""
    match = re.match(r"^[A-Za-z0-9_.-]+(.*)$", requirement)
    if match is None or not match.group(1):
        return ""
    return match.group(1).replace(" ", "")


def dependency_name(requirement: str) -> str:
    """Return a normalized distribution name from a requirement string."""
    match = re.match(r"([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        return ""
    return normalize_package_name(match.group(1))


def normalize_package_name(name: str) -> str:
    """Normalize a distribution name according to PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_prerelease_version(version: str) -> bool:
    """Return whether an accepted X.Y.Z release has an aN, bN, or rcN suffix."""
    match = VERSION_RE.fullmatch(version)
    if match is None:
        raise ReleaseCheckError(
            "unsafe or invalid release version input "
            "(expected X.Y.Z with optional aN, bN, or rcN suffix): "
            f"{version!r}"
        )
    return match.group("prerelease") is not None


def project_metadata(pyproject_path: Path) -> dict[str, Any]:
    """Read the project metadata using the supported Python TOML parser."""
    return load_toml(pyproject_path)["project"]


def verify_version(
    version: str,
    *,
    pyproject_path: Path = PYPROJECT,
) -> None:
    """Verify a release version against project metadata."""
    if VERSION_RE.fullmatch(version) is None:
        raise ReleaseCheckError(f"unsafe or invalid release version input: {version!r}")

    project = project_metadata(pyproject_path)
    actual = str(project["version"])
    if actual != version:
        raise ReleaseCheckError(
            f"workflow input version {version!r} does not match pyproject.toml {actual!r}"
        )
    print(f"ok: package metadata version matches workflow input {version}")
