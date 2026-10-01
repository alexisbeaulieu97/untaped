"""Pure release candidate and resumable publication decisions."""

import hashlib
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from _release_core import (
    FULL_SHA_RE,
    PYPROJECT,
    SHA256_RE,
    ReleaseCheckError,
    normalize_package_name,
    project_metadata,
    verify_version,
)

_normalize_package_name = normalize_package_name
_project_metadata = project_metadata

# GitHub's release list and PyPI's CDN-cached simple index lag writes by a few
# seconds; poll with backoff (2+4+8+16+32 = 62s) before calling a write lost.
_VISIBILITY_ATTEMPTS = 6
_VISIBILITY_FIRST_DELAY = 2.0
_sleep: Callable[[float], None] = time.sleep


def wait_for[T](probe: Callable[[], T | None]) -> T | None:
    """Return ``probe()`` once it is not ``None``, retrying with backoff; else ``None``."""
    delay = _VISIBILITY_FIRST_DELAY
    for attempt in range(_VISIBILITY_ATTEMPTS):
        value = probe()
        if value is not None or attempt == _VISIBILITY_ATTEMPTS - 1:
            return value
        _sleep(delay)
        delay *= 2
    return None


class PublicationState(StrEnum):
    """Ordered states in the restartable production publication contract."""

    VALIDATED = "validated"
    GITHUB_DRAFT = "github-draft"
    INDEX_UPLOADED = "index-uploaded"
    PUBLISHED_SMOKE = "published-smoke"
    GITHUB_PUBLISHED = "github-published"


@dataclass(frozen=True)
class ReleaseArtifact:
    """One immutable wheel or source archive in a release candidate."""

    filename: str
    sha256: str
    path: Path

    def __post_init__(self) -> None:
        if not SHA256_RE.fullmatch(self.sha256):
            raise ReleaseCheckError(f"invalid SHA-256 for {self.filename}: {self.sha256!r}")
        if self.path.name != self.filename:
            raise ReleaseCheckError(
                f"artifact path/name mismatch: {self.path.name!r} != {self.filename!r}"
            )


@dataclass(frozen=True)
class ReleaseCandidate:
    """Validated release identity and exact local distribution set."""

    package_name: str
    version: str
    candidate_oid: str
    artifacts: tuple[ReleaseArtifact, ...]

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def artifact_hashes(self) -> dict[str, str]:
        return {artifact.filename: artifact.sha256 for artifact in self.artifacts}


@dataclass(frozen=True)
class GitHubRelease:
    """The GitHub release identity needed for safe resumption."""

    release_id: str
    tag: str
    target_oid: str
    draft: bool
    assets: Mapping[str, str]


class PublicationTransport(Protocol):
    """Remote boundary used by the publication state machine.

    Production adapters are deliberately outside the state logic. Tests and
    local rehearsals inject a fake transport, so no live release operation is
    needed to prove the ordering and resume rules.
    """

    def inspect_github_release(self, *, tag: str) -> GitHubRelease | None: ...

    def inspect_tag_target(self, *, tag: str) -> str | None: ...

    def create_github_draft(self, candidate: ReleaseCandidate) -> GitHubRelease: ...

    def inspect_github_assets(self, release: GitHubRelease) -> Mapping[str, str]: ...

    def upload_github_asset(self, release: GitHubRelease, artifact: ReleaseArtifact) -> None: ...

    def inspect_index(
        self, *, index: str, candidate: ReleaseCandidate
    ) -> Mapping[str, str] | None: ...

    def upload_index(self, *, index: str, candidate: ReleaseCandidate) -> None: ...

    def smoke_published(self, *, index: str, candidate: ReleaseCandidate) -> None: ...

    def publish_github_release(self, release: GitHubRelease) -> GitHubRelease: ...


def collect_release_candidate(
    *,
    version: str,
    candidate_oid: str,
    dist_dir: Path,
    current_oid: str | None = None,
    pyproject_path: Path = PYPROJECT,
) -> ReleaseCandidate:
    """Validate the reviewed commit, metadata, and exact wheel/sdist set."""
    if not FULL_SHA_RE.fullmatch(candidate_oid):
        raise ReleaseCheckError("candidate OID must be a lowercase 40-character commit SHA")
    if current_oid is not None:
        verify_candidate_oid(candidate_oid, current_oid)
    verify_version(version, pyproject_path=pyproject_path)
    if not dist_dir.is_dir():
        raise ReleaseCheckError(f"distribution directory is missing: {dist_dir}")

    project = _project_metadata(pyproject_path)
    package_name = str(project["name"])
    files = sorted(path for path in dist_dir.iterdir() if path.is_file())
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith((".tar.gz", ".zip"))]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        names = ", ".join(path.name for path in files) or "<empty>"
        raise ReleaseCheckError(
            "release candidate must contain exactly one wheel and one source archive; "
            f"found {names}"
        )

    artifacts: list[ReleaseArtifact] = []
    if not _wheel_matches_project(wheels[0], package_name, version):
        raise ReleaseCheckError(
            f"artifact {wheels[0].name!r} does not match {package_name} {version}"
        )
    if not _sdist_matches_project(sdists[0], package_name, version):
        raise ReleaseCheckError(
            f"artifact {sdists[0].name!r} does not match {package_name} {version}"
        )
    for path in (wheels[0], sdists[0]):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts.append(ReleaseArtifact(path.name, digest, path))
    return ReleaseCandidate(package_name, version, candidate_oid, tuple(artifacts))


def _wheel_matches_project(path: Path, package_name: str, version: str) -> bool:
    if path.suffix != ".whl":
        return False
    parts = path.stem.rsplit("-", maxsplit=4)
    if len(parts) != 5:
        return False
    distribution, file_version, python_tag, abi_tag, platform_tag = parts
    del python_tag, abi_tag, platform_tag
    return (
        _normalize_package_name(distribution) == _normalize_package_name(package_name)
        and file_version == version
    )


def _sdist_matches_project(path: Path, package_name: str, version: str) -> bool:
    if path.name.endswith(".tar.gz"):
        stem = path.name.removesuffix(".tar.gz")
    elif path.suffix == ".zip":
        stem = path.stem
    else:
        return False
    distribution, separator, file_version = stem.rpartition("-")
    return bool(separator) and (
        _normalize_package_name(distribution) == _normalize_package_name(package_name)
        and file_version == version
    )


def verify_candidate_oid(candidate_oid: str, current_oid: str) -> None:
    """Require the dispatch's reviewed commit to be the checked-out commit."""
    if not FULL_SHA_RE.fullmatch(candidate_oid):
        raise ReleaseCheckError("candidate OID must be a lowercase 40-character commit SHA")
    if not FULL_SHA_RE.fullmatch(current_oid):
        raise ReleaseCheckError("checked-out OID must be a lowercase 40-character commit SHA")
    if candidate_oid != current_oid:
        raise ReleaseCheckError(
            f"checked-out commit {current_oid!r} does not match reviewed candidate "
            f"{candidate_oid!r}"
        )
    print(f"ok: checked-out commit matches reviewed candidate {candidate_oid}")


def run_publication(
    candidate: ReleaseCandidate,
    *,
    index: str,
    transport: PublicationTransport,
) -> PublicationState:
    """Run or resume the ordered publication state machine.

    Every remotely completed prefix is inspected before the next mutation. A
    published GitHub release is immutable for this operation: it is accepted
    only when its complete asset and index prefix is exact.
    """
    if index not in {"testpypi", "pypi"}:
        raise ReleaseCheckError(f"unknown release index: {index}")
    release: GitHubRelease | None = None
    if index == "pypi":
        release = _ensure_github_prefix(candidate, transport=transport)

    missing = _index_missing(candidate, index=index, transport=transport)
    if missing:
        transport.upload_index(index=index, candidate=candidate)
        _require_index_prefix(candidate, index=index, transport=transport)
    transport.smoke_published(index=index, candidate=candidate)
    if index == "testpypi":
        return PublicationState.PUBLISHED_SMOKE

    assert release is not None
    _publish_github_release(candidate, transport=transport, index=index)
    return PublicationState.GITHUB_PUBLISHED


def ensure_github_draft(
    candidate: ReleaseCandidate, *, transport: PublicationTransport
) -> GitHubRelease:
    """Create or resume the exact GitHub prefix for a candidate.

    A completed, exact release is a valid resumable prefix. Returning it as a
    no-op lets a rerun continue through index verification and published smoke
    without attempting to repair an immutable release.
    """
    return _ensure_github_prefix(candidate, transport=transport)


def verify_index_artifacts(
    candidate: ReleaseCandidate, *, index: str, transport: PublicationTransport
) -> None:
    """Verify the immutable index filename-to-SHA set after a trusted upload."""
    _require_index_prefix(candidate, index=index, transport=transport)


def publish_github_draft(
    candidate: ReleaseCandidate,
    *,
    transport: PublicationTransport,
    index: str = "pypi",
) -> GitHubRelease:
    """Publish one exact draft, or verify an exact completed prefix as a no-op."""
    return _publish_github_release(candidate, transport=transport, index=index)


def _inspect_github_prefix(
    candidate: ReleaseCandidate, *, transport: PublicationTransport
) -> tuple[GitHubRelease, set[str]]:
    """Inspect the exact release/tag/asset prefix shared by every transition."""
    release = _require_release(
        wait_for(lambda: transport.inspect_github_release(tag=candidate.tag)), candidate
    )
    _verify_release_identity(
        release,
        candidate,
        transport=transport,
        allow_missing_tag=release.draft,
    )
    missing = _verify_hash_map(
        expected=candidate.artifact_hashes,
        actual=transport.inspect_github_assets(release),
        label="GitHub assets",
    )
    if not release.draft and missing:
        raise ReleaseCheckError("published GitHub release is missing an expected asset")
    return release, missing


def _ensure_github_prefix(
    candidate: ReleaseCandidate, *, transport: PublicationTransport
) -> GitHubRelease:
    """Create or repair a draft until its exact immutable prefix is visible."""
    existing = transport.inspect_github_release(tag=candidate.tag)
    if existing is None:
        _verify_existing_tag_target(transport, candidate)
        transport.create_github_draft(candidate)

    release, missing = _inspect_github_prefix(candidate, transport=transport)
    if not release.draft:
        return release

    for artifact in candidate.artifacts:
        if artifact.filename in missing:
            transport.upload_github_asset(release, artifact)
    complete, remaining = _inspect_github_prefix(candidate, transport=transport)
    if not complete.draft:
        raise ReleaseCheckError("GitHub release changed from draft during asset upload")
    if remaining:
        raise ReleaseCheckError(f"GitHub assets are missing: {', '.join(sorted(remaining))}")
    return complete


def _index_missing(
    candidate: ReleaseCandidate,
    *,
    index: str,
    transport: PublicationTransport,
) -> set[str]:
    """Return missing index files after rejecting extras and conflicting hashes."""
    actual = transport.inspect_index(index=index, candidate=candidate)
    if actual is None:
        return set(candidate.artifact_hashes)
    return _verify_hash_map(
        expected=candidate.artifact_hashes,
        actual=actual,
        label=f"{index} files",
    )


def _require_index_prefix(
    candidate: ReleaseCandidate,
    *,
    index: str,
    transport: PublicationTransport,
) -> None:
    """Require one exact index prefix after a trusted upload or on resume."""

    def complete() -> Mapping[str, str] | None:
        actual = transport.inspect_index(index=index, candidate=candidate)
        return actual if actual is not None and actual.keys() >= expected.keys() else None

    expected = candidate.artifact_hashes
    actual = wait_for(complete) or transport.inspect_index(index=index, candidate=candidate)
    _require_exact_hash_map(actual, candidate.artifact_hashes, f"{index} files")


def _publish_github_release(
    candidate: ReleaseCandidate,
    *,
    transport: PublicationTransport,
    index: str,
) -> GitHubRelease:
    """Publish an exact draft, with an exact published prefix as a no-op."""
    release, missing = _inspect_github_prefix(candidate, transport=transport)
    if missing:
        raise ReleaseCheckError(f"GitHub assets are missing: {', '.join(sorted(missing))}")
    _require_index_prefix(candidate, index=index, transport=transport)
    if not release.draft:
        return release

    published = transport.publish_github_release(release)
    _verify_release_identity(published, candidate, transport=transport)
    if published.draft:
        raise ReleaseCheckError("GitHub release publish did not clear the draft state")
    return published


def _verify_release_identity(
    release: GitHubRelease,
    candidate: ReleaseCandidate,
    *,
    transport: PublicationTransport | None = None,
    allow_missing_tag: bool = False,
) -> None:
    if release.tag != candidate.tag:
        raise ReleaseCheckError(
            f"GitHub release tag {release.tag!r} does not match {candidate.tag!r}"
        )
    if release.target_oid != candidate.candidate_oid:
        raise ReleaseCheckError(
            f"GitHub release target {release.target_oid!r} does not match candidate "
            f"{candidate.candidate_oid!r}"
        )
    if transport is not None:
        actual_target = transport.inspect_tag_target(tag=candidate.tag)
        if actual_target is None and allow_missing_tag and release.draft:
            return
        if actual_target != candidate.candidate_oid:
            raise ReleaseCheckError(
                f"Git tag {candidate.tag} resolves to {actual_target!r}, expected "
                f"{candidate.candidate_oid!r}"
            )


def _verify_existing_tag_target(
    transport: PublicationTransport, candidate: ReleaseCandidate
) -> None:
    actual_target = transport.inspect_tag_target(tag=candidate.tag)
    if actual_target is not None and actual_target != candidate.candidate_oid:
        raise ReleaseCheckError(
            f"existing Git tag {candidate.tag} resolves to {actual_target!r}, expected "
            f"{candidate.candidate_oid!r}"
        )


def _require_release(release: GitHubRelease | None, candidate: ReleaseCandidate) -> GitHubRelease:
    if release is None:
        raise ReleaseCheckError(f"GitHub release {candidate.tag} disappeared during resume")
    return release


def _verify_hash_map(
    *, expected: Mapping[str, str], actual: Mapping[str, str], label: str
) -> set[str]:
    extra = sorted(set(actual) - set(expected))
    if extra:
        raise ReleaseCheckError(f"{label} contain unexpected files: {', '.join(extra)}")
    mismatched = sorted(
        name for name, digest in actual.items() if name in expected and digest != expected[name]
    )
    if mismatched:
        raise ReleaseCheckError(f"{label} have conflicting SHA-256 for: {', '.join(mismatched)}")
    return set(expected) - set(actual)


def _require_exact_hash_map(
    actual: Mapping[str, str] | None,
    expected: Mapping[str, str],
    label: str,
) -> None:
    if actual is None:
        raise ReleaseCheckError(f"{label} are not visible after the upload")
    missing = _verify_hash_map(expected=expected, actual=actual, label=label)
    if missing:
        raise ReleaseCheckError(f"{label} are missing: {', '.join(sorted(missing))}")


def _filename_matches_candidate(filename: str, candidate: ReleaseCandidate) -> bool:
    """Identify every same-project/version anchor, including unexpected files."""
    normalized = filename.replace("_", "-").lower()
    package = _normalize_package_name(candidate.package_name)
    prefix = f"{package}-{candidate.version.lower()}"
    suffix = normalized[len(prefix) :] if normalized.startswith(prefix) else ""
    return bool(suffix) and suffix.startswith(("-", ".", "_"))


def prepare_index_upload(
    candidate: ReleaseCandidate,
    *,
    index: str,
    transport: PublicationTransport,
    output_dir: Path,
) -> bool:
    """Copy only files proven missing from an index prefix into ``output_dir``."""
    missing = _index_missing(candidate, index=index, transport=transport)
    if not missing:
        print("index-prefix-exact=true")
        return True
    output_dir.mkdir(parents=True, exist_ok=True)
    for artifact in candidate.artifacts:
        if artifact.filename in missing:
            shutil.copy2(artifact.path, output_dir / artifact.filename)
    print("index-prefix-exact=false")
    print(f"index-prefix-missing={','.join(sorted(missing))}")
    return False
