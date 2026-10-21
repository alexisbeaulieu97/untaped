"""Release checks and steps for the release workflow (``.github/workflows/release.yml``).

Usage: ``uv run python scripts/release.py [--root DIR] <command> ...``. Each
subcommand is one step of the workflow:

- ``route --event E --ref-type T --ref-name N --repository OWNER/REPO
  [--before SHA]`` decides what a run does and prints ``version``, ``index``
  (``pypi``, ``testpypi`` or ``none``), ``tag`` and ``reason`` as
  ``key=value`` lines for ``$GITHUB_OUTPUT``: a push to ``main`` that changes
  the version releases it, a pushed ``v*`` tag on ``main`` releases (the
  fallback), a dispatch is a TestPyPI rehearsal, and anything else on
  ``main`` or in a fork publishes nothing.
- ``version`` prints the ``untaped`` package's version (CI's wheel jobs).
- ``readmes VERSION`` rewrites, in the checkout, each package README's
  ``blob/main`` and ``tree/main`` links into this repository to ``vVERSION``
  (just before the build), so a release's PyPI page keeps describing that
  release after the docs move on ``main``. The committed READMEs keep
  ``main``, which is right while browsing the repository.
- ``check [VERSION] [--dist DIR]`` checks every package version and sibling
  pin against VERSION (default: the ``untaped`` package's version) before the
  build, and with ``--dist`` the built artifact list and that no built
  package's metadata (its PyPI page) links to ``main`` (after the build).
- ``notes VERSION`` prints the CHANGELOG section for the GitHub release body,
  its links pinned to ``vVERSION`` as ``readmes`` pins a README's.
- ``index VERSION --dist DIR --index pypi|testpypi [--complete]`` compares the
  built files with what the index already holds: before publishing (conflicts
  fail, so a re-run uploads only what is missing) and, with ``--complete``,
  after publishing (waits until every file is there).
- ``smoke UNTAPED_EXE VERSION [--expect A,B] [--quarantined C] [--agents]``
  runs an installed ``untaped`` and checks its version, that its plugins
  are exactly the expected ones ready (default: every first-party entry point)
  and the quarantined ones quarantined, every ``--help``, and with
  ``--agents`` each expected plugin's skill and every ``untaped agents`` page:
  the map, each plugin's page and each guide a page lists (a guide missing
  from the wheel fails here).
- ``tag TAG --repo OWNER/REPO`` creates the annotated tag TAG on the
  checkout's commit once PyPI has the verified files, or confirms it is
  already there; a ``v*`` tag on another commit is an error, since tags never
  move.
- ``github-release VERSION --tag TAG --repo OWNER/REPO --dist DIR --notes FILE``
  creates, completes or verifies the GitHub release (the last step).

Every failure prints one line per error on stderr, nothing on stdout, and
exits 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from untaped.git import GitCommandError, git_toplevel, run_git

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FORMAT = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?$")
INDEX_URLS = {
    "pypi": "https://pypi.org/pypi/{name}/{version}/json",
    "testpypi": "https://test.pypi.org/pypi/{name}/{version}/json",
}
#: This repository on GitHub; links into it are ``REPO_URL/<kind>/<ref>/...``.
REPO_URL = "https://github.com/alexisbeaulieu97/untaped"
#: The one repository whose release runs publish; a fork's runs route to ``none``.
PUBLISHING_REPOSITORY = REPO_URL.removeprefix("https://github.com/")
#: The identity on the tags the release run creates (``tag``).
TAGGER = {
    "name": "github-actions[bot]",
    "email": "41898282+github-actions[bot]@users.noreply.github.com",
}
#: A ``blob/main`` or ``tree/main`` link into this repository; group 1 is all before ``/main``.
MAIN_LINK = re.compile(rf"({re.escape(REPO_URL)}/(?:blob|tree))/main(?![\w-])")
#: ``index --complete`` retries this often, this many seconds apart (about 2 minutes).
INDEX_TRIES = 12
INDEX_DELAY = 10

Fetch = Callable[[str], dict[str, Any] | None]
Gh = Callable[..., subprocess.CompletedProcess[str]]


class ReleaseError(Exception):
    """A user-facing release check failure."""


# --- packages and versions --------------------------------------------------


def _pyproject(directory: Path) -> dict[str, Any]:
    path = directory / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _member_dirs(root: Path) -> list[Path]:
    data = _pyproject(root)
    workspace = data.get("tool", {}).get("uv", {}).get("workspace", {})
    excluded = {path for pattern in workspace.get("exclude", []) for path in root.glob(pattern)}
    members = {path for pattern in workspace.get("members", []) for path in root.glob(pattern)}
    return sorted(
        path for path in members - excluded if path.is_dir() and (path / "pyproject.toml").exists()
    )


def _package_dirs(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """``{canonical name: (directory, [project] table)}``; see ``packages``."""
    found: dict[str, tuple[Path, dict[str, Any]]] = {}
    for directory in [root, *_member_dirs(root)]:
        project = _pyproject(directory).get("project")
        if project is None:
            continue
        name = canonicalize_name(project["name"])
        if name in found:
            dirs = sorted(str(d.relative_to(root)) for d in (found[name][0], directory))
            raise ReleaseError(f"two packages are named {name}: {', '.join(dirs)}")
        found[name] = (directory, project)
    return found


def packages(root: Path) -> dict[str, dict[str, Any]]:
    """``{canonical name: [project] table}`` for every package in the repo.

    The root ``[project]`` (if any) plus each directory matched by
    ``[tool.uv.workspace].members`` minus ``exclude`` that has a
    ``pyproject.toml`` with ``[project]``. Raises ``ReleaseError`` on a
    duplicate canonical name.
    """
    found = _package_dirs(root)
    return {name: found[name][1] for name in sorted(found)}


def _format_errors(version: str) -> list[str]:
    if VERSION_FORMAT.fullmatch(version):
        return []
    return [f"version {version} is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN"]


def release_version(root: Path) -> str:
    """The ``untaped`` package's version; ``ReleaseError`` if absent or not ``X.Y.Z[(a|b|rc)N]``."""
    project = packages(root).get("untaped")
    if project is None:
        raise ReleaseError(f"no package named untaped under {root}")
    version = str(project.get("version"))
    if errors := _format_errors(version):
        raise ReleaseError(errors[0])
    return version


def baseline_tag(tags: Iterable[str], *, prereleases: bool = False) -> str | None:
    """The newest release among ``tags`` (``vX.Y.Z``; never a dev release, never another name).

    Release candidates (``vX.Y.ZrcN``) count only with ``prereleases=True``:
    a promise is what a final release showed (the surface checks), while the
    changelog build compares with whatever was released last.
    """
    found: list[tuple[Version, str]] = []
    for tag in tags:
        if not tag.startswith("v"):
            continue
        try:
            version = Version(tag.removeprefix("v"))
        except InvalidVersion:
            continue
        if not version.is_devrelease and (prereleases or not version.is_prerelease):
            found.append((version, tag))
    return max(found)[1] if found else None


def _toplevel(root: Path) -> Path:
    """The checkout holding ``root``; ``ReleaseError`` outside one."""
    try:
        top = git_toplevel(root)
    except GitCommandError as exc:
        raise ReleaseError(str(exc)) from exc
    if top is None:
        raise ReleaseError(f"{root} is not a git checkout")
    return top


def _head(top: Path) -> str:
    """The commit checked out in ``top`` (a tag's commit, never the tag object)."""
    return run_git(["rev-parse", "HEAD^{commit}"], cwd=top, timeout=30, capture=True).text.strip()


def head_commit(root: Path) -> str:
    """``_head`` of the checkout holding ``root``."""
    top = _toplevel(root)
    try:
        return _head(top)
    except GitCommandError as exc:
        raise ReleaseError(str(exc)) from exc


def check_on_main(root: Path) -> None:
    """Fetch ``origin``'s ``main`` and fail unless HEAD is on it (an ancestor or its tip)."""
    top = _toplevel(root)
    try:
        run_git(
            ["fetch", "--no-tags", "origin", "main"], cwd=top, timeout=120, retry_transient=True
        )
        head = _head(top)
        ancestor = run_git(
            ["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], cwd=top, timeout=30, check=False
        )
    except GitCommandError as exc:
        raise ReleaseError(str(exc)) from exc
    if ancestor.returncode:
        raise ReleaseError(f"commit {head} is not on main")


def _requirements(project: dict[str, Any]) -> Iterator[str]:
    """Dependencies, then each extra in file order."""
    yield from project.get("dependencies", [])
    for extra in project.get("optional-dependencies", {}).values():
        yield from extra


def _is_exact_pin(requirement: Requirement, version: str) -> bool:
    specs = list(requirement.specifier)
    return (
        requirement.url is None
        and len(specs) == 1
        and specs[0].operator == "=="
        and specs[0].version == version
    )


def _pin_errors(name: str, project: dict[str, Any], siblings: set[str], version: str) -> list[str]:
    errors = []
    for written in _requirements(project):
        try:
            requirement = Requirement(written)
        except InvalidRequirement:
            errors.append(f"{name} has an invalid requirement {written}")
            continue
        sibling = canonicalize_name(requirement.name)
        if sibling in siblings - {name} and not _is_exact_pin(requirement, version):
            errors.append(f"{name} pins {written}, not =={version}")
    return errors


def version_errors(found: dict[str, dict[str, Any]], version: str) -> list[str]:
    """Every package in ``found`` (``packages``) at ``version``; every sibling pin ``==version``."""
    errors = _format_errors(version)
    for name, project in found.items():
        if project.get("version") != version:
            errors.append(f"{name} is at {project.get('version')}, not {version}")
        errors += _pin_errors(name, project, set(found), version)
    return errors


# --- routing ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Route:
    """What a release run does: ``route``'s answer, one ``key=value`` output each."""

    version: str
    #: ``pypi``, ``testpypi``, or ``none`` (nothing is built or published).
    index: str
    #: ``vVERSION`` for ``pypi``; empty otherwise.
    tag: str
    reason: str

    def lines(self) -> str:
        """The ``key=value`` lines for ``$GITHUB_OUTPUT``."""
        return "".join(f"{key}={value}\n" for key, value in dataclasses.asdict(self).items())


def route(
    root: Path, *, event: str, ref_type: str, ref_name: str, repository: str, before: str
) -> Route:
    """Decide what the release run started by this event does.

    A fork publishes nothing; a dispatch is a TestPyPI rehearsal; a pushed
    ``v<version>`` tag on ``main`` releases; a push to ``main`` releases
    when it changed the version (``_route_main``). Anything else is an error.
    The version is read and checked first, whatever the event.
    """
    version = release_version(root)
    if repository != PUBLISHING_REPOSITORY:
        reason = f"{repository} is not the publishing repository: nothing to publish"
        return Route(version, "none", "", reason)
    match event, ref_type, ref_name:
        case "workflow_dispatch", _, _:
            return Route(version, "testpypi", "", "rehearsal: TestPyPI only")
        case "push", "tag", _:
            if ref_name != f"v{version}":
                raise ReleaseError(f"tag {ref_name} does not match the package version {version}")
            check_on_main(root)
            return Route(version, "pypi", ref_name, f"{ref_name} is on main: releasing {version}")
        case "push", "branch", "main":
            return _route_main(root, version, before)
    raise ReleaseError(f"nothing releases from a {event} of {ref_type} {ref_name}")


def _route_main(root: Path, version: str, before: str) -> Route:
    """A push to ``main``: release the version this push introduced, once.

    The tag already at HEAD (a re-run, or a hand-pushed tag on this commit)
    releases again, idempotently. Otherwise only a version this push changed
    (compared with ``before``) releases, and only while its tag is absent.
    """
    tag = f"v{version}"
    top = _toplevel(root)
    try:
        head = _head(top)
        tagged = _remote_tag_commit(top, tag)
        if tagged == head:
            reason = (
                f"{tag} is already at this commit (a re-run or a hand tag): releasing {version}"
            )
            return Route(version, "pypi", tag, reason)
        previous = _version_at(root, top, before)
    except GitCommandError as exc:
        raise ReleaseError(str(exc)) from exc
    if previous == version:
        untagged = "" if tagged else " and not released yet"
        reason = f"{version} is unchanged by this push{untagged}: nothing to publish"
        return Route(version, "none", "", reason)
    if tagged:
        reason = f"{tag} already points at {tagged[:7]}, not this commit: nothing to publish"
        return Route(version, "none", "", reason)
    since = "is new" if previous is None else f"replaces {previous} at {before[:7]}"
    return Route(version, "pypi", tag, f"{version} {since} and has no tag yet: releasing it")


def _remote_tag_commit(top: Path, tag: str) -> str | None:
    """The commit ``origin``'s ``refs/tags/<tag>`` names, or None when it has no such tag.

    An annotated tag lists a second, peeled ``^{}`` line naming its commit;
    a lightweight tag's only line already names it.
    """
    ref = f"refs/tags/{tag}"
    listed = run_git(
        ["ls-remote", "--tags", "origin", ref, f"{ref}^{{}}"],
        cwd=top,
        timeout=60,
        capture=True,
        retry_transient=True,
    )
    shas = {name: sha for sha, name in (line.split("\t", 1) for line in listed.text.splitlines())}
    return shas.get(f"{ref}^{{}}", shas.get(ref))


def _version_at(root: Path, top: Path, before: str) -> str | None:
    """The ``untaped`` package's version at commit ``before``.

    None when ``before`` is all zeros (a branch's first push) or the package
    file did not exist there. A ``before`` the checkout lacks is an error.
    """
    if not before:
        raise ReleaseError("a push to main needs --before")
    if set(before) == {"0"}:
        return None
    directory = _package_dirs(root)["untaped"][0]
    path = (directory / "pyproject.toml").resolve().relative_to(top).as_posix()
    if run_git(
        ["cat-file", "-e", f"{before}^{{commit}}"], cwd=top, timeout=30, check=False
    ).returncode:
        raise ReleaseError(f"commit {before} is not in this checkout")
    shown = run_git(["show", f"{before}:{path}"], cwd=top, timeout=30, capture=True, check=False)
    if shown.returncode:
        return None
    try:
        return str(tomllib.loads(shown.text).get("project", {}).get("version"))
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseError(f"{path} at {before} is not valid TOML: {exc}") from exc


# --- artifacts --------------------------------------------------------------


def _stem(name: str, version: str) -> str:
    return f"{name.replace('-', '_')}-{version}"


def _package_artifacts(name: str, version: str) -> tuple[str, str]:
    stem = _stem(name, version)
    return f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"


def expected_artifacts(names: Collection[str], version: str) -> set[str]:
    """One wheel and one sdist per package name.

    Names use the canonical name with ``-`` as ``_`` (wheel and PEP 625 sdist
    naming). The wheel tag is always ``py3-none-any``: every package is pure
    Python.
    """
    return {file for name in names for file in _package_artifacts(name, version)}


def dist_files(dist: Path) -> set[str]:
    """The names of the files directly in ``dist``, dotfiles included."""
    if not dist.is_dir():
        raise ReleaseError(f"{dist} is not a directory")
    return {path.name for path in dist.iterdir() if path.is_file()}


def artifact_errors(names: Collection[str], version: str, found: set[str]) -> list[str]:
    """``missing:`` and ``unexpected:`` files among ``found`` (from ``dist_files``)."""
    expected = expected_artifacts(names, version)
    return [f"missing: {file}" for file in sorted(expected - found)] + [
        f"unexpected: {file}" for file in sorted(found - expected)
    ]


# --- README links -----------------------------------------------------------


def pin_links(text: str, version: str) -> str:
    """``text`` with each ``blob/main`` and ``tree/main`` link into this repo at ``v<version>``."""
    return MAIN_LINK.sub(rf"\1/v{version}", text)


def readme_paths(root: Path) -> list[Path]:
    """Each package's ``[project].readme`` file (a path or a ``{file = ...}`` table)."""
    paths = []
    for directory in [root, *_member_dirs(root)]:
        readme = _pyproject(directory).get("project", {}).get("readme")
        if isinstance(readme, dict):
            readme = readme.get("file")
        if isinstance(readme, str):
            paths.append(directory / readme)
    return paths


def pin_readmes(root: Path, version: str) -> int:
    """Rewrite every package README with ``pin_links``; the number of files it changed."""
    if errors := _format_errors(version):
        raise ReleaseError(errors[0])
    changed = 0
    for path in readme_paths(root):
        text = path.read_text(encoding="utf-8")
        pinned = pin_links(text, version)
        if pinned != text:
            path.write_text(pinned, encoding="utf-8")
            changed += 1
    return changed


def _metadata(dist: Path, stem: str, file: str) -> str:
    """The core metadata of a built wheel (``METADATA``) or sdist (``PKG-INFO``)."""
    if file.endswith(".whl"):
        with zipfile.ZipFile(dist / file) as wheel:
            return wheel.read(f"{stem}.dist-info/METADATA").decode("utf-8")
    with tarfile.open(dist / file) as sdist:
        member = sdist.extractfile(f"{stem}/PKG-INFO")
        if member is None:
            raise KeyError(f"{stem}/PKG-INFO is not a file")
        return member.read().decode("utf-8")


def main_link_errors(names: Collection[str], version: str, dist: Path) -> list[str]:
    """One error per built wheel or sdist in ``dist`` whose metadata links to ``main``.

    The metadata holds the README as the PyPI page; ``readmes`` pins its links
    before the build. A missing artifact is ``artifact_errors``' to report.
    """
    errors = []
    for name in sorted(names):
        stem = _stem(name, version)
        for file in _package_artifacts(name, version):
            if not (dist / file).is_file():
                continue
            try:
                text = _metadata(dist, stem, file)
            except (OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
                reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                errors.append(f"{file}: cannot read its metadata: {reason}")
                continue
            if match := MAIN_LINK.search(text):
                errors.append(f"{file} links to main: {match.group(0)}")
    return errors


# --- index ------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_json(url: str) -> dict[str, Any] | None:
    """The index's JSON for ``url``; None on 404. Any other failure raises ``ReleaseError``."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ReleaseError(f"could not read {url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ReleaseError(f"could not read {url}: {exc}") from exc
    except ValueError as exc:
        raise ReleaseError(f"{url} did not return JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseError(f"{url} did not return a JSON object")
    return data


def _remote_digests(url: str, payload: dict[str, Any] | None) -> dict[str, str]:
    """``{filename: sha256}`` from an index payload; ``ReleaseError`` if it lacks them."""
    if payload is None:
        return {}
    try:
        return {row["filename"]: row["digests"]["sha256"] for row in payload["urls"]}
    except (KeyError, TypeError) as exc:
        raise ReleaseError(f"{url} has no file list (urls[].filename, digests.sha256)") from exc


def _local_digests(root: Path, version: str, dist: Path) -> dict[str, dict[str, str]]:
    """``{package: {built file: sha256}}`` for every package, each file hashed once."""
    files = dist_files(dist)
    return {
        name: {
            file: _sha256(dist / file)
            for file in sorted(set(_package_artifacts(name, version)) & files)
        }
        for name in packages(root)
    }


def _package_index_errors(
    url: str, local: dict[str, str], *, index: str, complete: bool, fetch: Fetch
) -> tuple[list[str], int, int]:
    """``index_errors`` for one package's index entry at ``url``."""
    errors: list[str] = []
    present = to_upload = 0
    remote = _remote_digests(url, fetch(url))
    for file, digest in local.items():
        if file not in remote:
            to_upload += 1
            if complete:
                errors.append(f"missing on {index}: {file}")
            continue
        present += 1
        if remote[file] != digest:
            errors.append(f"conflict: {file} on {index} has sha256 {remote[file]}, local {digest}")
    errors += [f"unexpected on {index}: {file}" for file in sorted(set(remote) - set(local))]
    return errors, present, to_upload


def _only_missing(errors: list[str]) -> bool:
    return all(error.startswith("missing on ") for error in errors)


def index_errors(
    root: Path,
    version: str,
    dist: Path,
    *,
    index: str,
    complete: bool,
    fetch: Fetch,
    sleep: Callable[[float], object] = time.sleep,
) -> tuple[list[str], int, int]:
    """(errors, present, to_upload). ``fetch(url)`` returns the parsed JSON, or None on 404.

    ``present`` counts local files the index holds (a conflicting one too, and
    it is reported); ``to_upload`` counts local files it does not hold yet.
    With ``complete`` a missing file is an error, and the check is retried
    (re-fetching only the packages still missing files) while files are only
    missing. A conflict, an unexpected file or a non-404 fetch failure ends it
    at once.
    """
    local = _local_digests(root, version, dist)
    results: dict[str, tuple[list[str], int, int]] = {}
    pending = list(local)
    for attempt in range(INDEX_TRIES):
        if attempt:
            sleep(INDEX_DELAY)
        for name in pending:
            url = INDEX_URLS[index].format(name=name, version=version)
            results[name] = _package_index_errors(
                url, local[name], index=index, complete=complete, fetch=fetch
            )
        failed = [name for name in pending if results[name][0]]
        if not failed or not all(_only_missing(results[name][0]) for name in failed):
            break
        pending = failed
    rows = [results[name] for name in local]
    errors = [error for row in rows for error in row[0]]
    return errors, sum(row[1] for row in rows), sum(row[2] for row in rows)


# --- notes ------------------------------------------------------------------


def release_notes(changelog: Path, version: str) -> str:
    """The stripped body of ``changelog``'s ``## <version>`` section, links pinned to its tag.

    The GitHub release page outlives ``main`` as a PyPI page does, so its
    ``blob/main`` links go through ``pin_links`` too.
    """
    lines = changelog.read_text(encoding="utf-8").splitlines()
    heading = f"## {version}"
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == heading) + 1
    except StopIteration:
        raise ReleaseError(f'{changelog.name} has no "{heading}" section') from None
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start:end]).strip()
    if not body:
        raise ReleaseError(f'{changelog.name}\'s "{heading}" section is empty')
    return pin_links(body, version)


# --- smoke ------------------------------------------------------------------


def smoke_errors(
    version_out: str,
    plugins_json: str,
    version: str,
    expected: Collection[str],
    quarantined: Collection[str] = (),
) -> list[str]:
    """The installed version, and exactly ``expected`` ready plus ``quarantined`` quarantined.

    A row for any other plugin, a plugin listed twice and a name in
    both sets are errors too.
    """
    errors = []
    if version_out.strip() != version:
        errors.append(f"untaped --version printed {version_out.strip()}, not {version}")
    try:
        rows = json.loads(plugins_json)
        names = [row["name"] for row in rows]
        statuses = {row["name"]: row["status"] for row in rows}
    except ValueError, TypeError, KeyError:
        return [*errors, "untaped plugin list --format json did not print a list of rows"]
    both = set(expected) & set(quarantined)
    errors += [f"plugin {name} is both expected and quarantined" for name in sorted(both)]
    errors += [f"plugin {name} is listed twice" for name in _duplicates(names)]
    for status, wanted in (("ready", expected), ("quarantined", quarantined)):
        for name in sorted(set(wanted) - both):
            if name not in statuses:
                errors.append(f"plugin {name} is missing")
            elif statuses[name] != status:
                unless = "" if status == "ready" else f", not {status}"
                errors.append(f"plugin {name} is {statuses[name]}{unless}")
    unexpected = set(statuses) - set(expected) - set(quarantined)
    errors += [f"plugin {name} is installed but not expected" for name in sorted(unexpected)]
    return errors


def _duplicates(names: Collection[str]) -> list[str]:
    return sorted(name for name, count in Counter(names).items() if count > 1)


def skill_errors(skills_json: str, expected: Collection[str]) -> list[str]:
    """The shell's ``untaped`` and ``untaped-<name>`` for each expected plugin,
    each listed once."""
    try:
        rows = json.loads(skills_json)
        names = [row["name"] for row in rows]
    except ValueError, TypeError, KeyError:
        return ["untaped skills list --format json did not print a list of rows"]
    errors = [f"skill {name} is listed twice" for name in _duplicates(names)]
    required = ["untaped", *(f"untaped-{name}" for name in sorted(expected))]
    errors += [f"skill {name} is missing" for name in required if name not in names]
    return errors


#: A topic line under a page's ``## Guides`` heading: ``- `name`: when to read it``.
_GUIDE_LINE = re.compile(r"^- `([^`]+)`")


def page_guides(page: str) -> list[str]:
    """The guides an ``untaped agents PLUGIN`` page lists."""
    guides: list[str] = []
    listing = False
    for line in page.splitlines():
        if line.startswith("## "):
            listing = line == "## Guides"
        elif listing and (match := _GUIDE_LINE.match(line)):
            guides.append(match.group(1))
    return guides


def plugin_guides(root: Path) -> dict[str, list[str]]:
    """``{plugin: guide names}``: the ``guides/*.md`` each package under ``root`` ships."""
    found: dict[str, list[str]] = {}
    for directory, project in _package_dirs(root).values():
        for name, target in project.get("entry-points", {}).get("untaped.plugins", {}).items():
            folder = directory / "src" / target.partition(":")[0].partition(".")[0] / "guides"
            found[name] = sorted(path.stem for path in folder.glob("*.md"))
    return found


def agents_errors(
    exe: str, expected: Collection[str], guides: Mapping[str, Collection[str]]
) -> list[str]:
    """Every ``untaped agents`` page that fails or prints nothing, and every guide left out.

    The map, then each expected plugin's page (core's too) and each guide it
    lists; a page that lists none of a guide ``guides`` names for its plugin
    means the guide is missing from the install.
    """
    errors: list[str] = []

    def page(*path: str) -> str:
        result = _run(exe, "agents", *path)
        command = " ".join(["untaped agents", *path])
        if result.returncode:
            errors.append(f"{command} exited {result.returncode}")
        elif not result.stdout.strip():
            errors.append(f"{command} printed nothing")
        return result.stdout

    page()
    for plugin in ["untaped", *sorted(expected)]:
        listed = page_guides(page(plugin))
        errors += [
            f"untaped agents {plugin} does not list the guide {guide}"
            for guide in sorted(set(guides.get(plugin, ())) - set(listed))
        ]
        for guide in listed:
            page(plugin, guide)
    return errors


def plugin_names(root: Path) -> list[str]:
    """Every plugin the packages under ``root`` declare as an entry point."""
    return sorted(
        name
        for project in packages(root).values()
        for name in project.get("entry-points", {}).get("untaped.plugins", {})
    )


def _run(exe: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([exe, *args], capture_output=True, text=True, check=False)


def run_smoke(
    exe: str,
    version: str,
    *,
    expect: Collection[str],
    quarantined: Collection[str] = (),
    agents: bool = False,
    guides: Mapping[str, Collection[str]] | None = None,
) -> tuple[list[str], int]:
    """Run the installed ``untaped``: (every smoke failure, expected plugin count)."""
    rows = _run(exe, "plugin", "list", "--format", "json")
    version_out = _run(exe, "--version").stdout
    errors = smoke_errors(version_out, rows.stdout, version, expect, quarantined)
    if rows.returncode:
        errors.append(f"untaped plugin list --format json exited {rows.returncode}")
    for command in [[], *([name] for name in sorted(expect))]:
        code = _run(exe, *command, "--help").returncode
        if code:
            errors.append(f"untaped {' '.join([*command, '--help'])} exited {code}")
    if agents:
        listing = _run(exe, "skills", "list", "--format", "json")
        errors += skill_errors(listing.stdout, expect)
        if listing.returncode:
            errors.append(f"untaped skills list --format json exited {listing.returncode}")
        errors += agents_errors(exe, expect, guides or {})
    return errors, len(expect)


def _names(value: str) -> list[str]:
    """A comma-separated name list; ``""`` is none."""
    return [name.strip() for name in value.split(",") if name.strip()]


# --- gh ---------------------------------------------------------------------


def _gh_list(gh: Gh, path: str, what: str) -> list[dict[str, Any]]:
    """Every item of the paginated GitHub list at ``path``; ``ReleaseError`` naming ``what``.

    ``--jq '.[]'`` prints one item per line on every page (``--slurp`` needs gh 2.48+).
    """
    result = gh("api", "--paginate", "--jq", ".[]", path)
    if result.returncode:
        raise ReleaseError(f"could not list {what}: {(result.stdout + result.stderr).strip()}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _gh_json(gh: Gh, *args: str) -> Any:
    """``gh api ARGS``'s JSON; ``ReleaseError`` on a non-zero exit."""
    result = gh("api", *args)
    if result.returncode:
        output = (result.stdout + result.stderr).strip()
        raise ReleaseError(f"gh api {args[-1]} failed: {output}")
    return json.loads(result.stdout)


# --- tag --------------------------------------------------------------------


def _tag_commit(tag: str, repo: str, gh: Gh) -> str | None:
    """The commit ``repo``'s tag ``tag`` names, or None when it has no such tag.

    ``matching-refs`` matches by prefix (``v11.0.0`` lists ``v11.0.0rc1``
    too), so only the exact ref counts. A lone ``GET git/ref`` would not
    tell a 404 from any other failure.
    """
    refs = _gh_list(gh, f"repos/{repo}/git/matching-refs/tags/{tag}", "tags")
    target = next((ref["object"] for ref in refs if ref.get("ref") == f"refs/tags/{tag}"), None)
    while target is not None and target["type"] == "tag":
        target = _gh_json(gh, f"repos/{repo}/git/tags/{target['sha']}")["object"]
    return None if target is None else str(target["sha"])


def _post_tag(tag: str, repo: str, head: str, gh: Gh) -> str | None:
    """Create the annotated tag object and its ref: None, or why the ref already exists."""
    fields = {
        "tag": tag,
        "message": f"untaped {tag.removeprefix('v')}",
        "object": head,
        "type": "commit",
        **{f"tagger[{key}]": value for key, value in TAGGER.items()},
    }
    flags = [arg for key, value in fields.items() for arg in ("-f", f"{key}={value}")]
    made = _gh_json(gh, "--method", "POST", *flags, f"repos/{repo}/git/tags")
    ref = ["-f", f"ref=refs/tags/{tag}", "-f", f"sha={made['sha']}"]
    result = gh("api", "--method", "POST", *ref, f"repos/{repo}/git/refs")
    output = (result.stdout + result.stderr).strip()
    if not result.returncode:
        return None
    if "Reference already exists" in output:
        return output
    raise ReleaseError(f"gh api repos/{repo}/git/refs failed: {output}")


def create_tag(tag: str, repo: str, head: str, *, gh: Gh) -> str:
    """Tag ``head`` as ``tag`` in ``repo`` (annotated, ``TAGGER``) unless it already is.

    Returns the line to print. A tag on another commit is an error: the
    ``v*`` ruleset forbids moving one. Losing a creation race to another run
    (the ref already exists) re-reads the tag once and judges it the same way.
    """
    if not tag.startswith("v") or _format_errors(tag[1:]):
        raise ReleaseError(f"tag {tag} is not v followed by a release version")
    found = _tag_commit(tag, repo, gh)
    if found is None:
        conflict = _post_tag(tag, repo, head, gh)
        if conflict is None:
            return f"tagged {tag} at {head}"
        found = _tag_commit(tag, repo, gh)
        if found is None:
            raise ReleaseError(f"could not create {tag}: {conflict}")
    if found != head:
        raise ReleaseError(
            f"{tag} points at {found}, not the built commit {head}; v* tags cannot move: "
            "release a patch version, or delete the wrong tag with the ruleset relaxed and re-run"
        )
    return f"{tag} already at {head}"


# --- GitHub release ---------------------------------------------------------


def _read_release(tag: str, repo: str, gh: Gh) -> dict[str, Any] | None:
    """The release for ``tag``, drafts included, or None when there is none.

    It lists releases because ``releases/tags/<tag>`` hides drafts.
    """
    releases = _gh_list(gh, f"repos/{repo}/releases", "releases")
    matches = [release for release in releases if release.get("tag_name") == tag]
    if len(matches) > 1:
        raise ReleaseError(f"{len(matches)} releases are tagged {tag}")
    return matches[0] if matches else None


def _checked(gh: Gh, *args: str) -> None:
    result = gh(*args)
    if result.returncode:
        output = (result.stdout + result.stderr).strip()
        raise ReleaseError(f"gh {' '.join(args[:2])} failed: {output}")


class AssetDiff(NamedTuple):
    """Release asset names against the local digests, in message order."""

    different: list[str]
    missing: list[str]
    unexpected: list[str]


def _asset_diff(release: dict[str, Any], digests: dict[str, str]) -> AssetDiff:
    remote = {asset["name"]: asset.get("digest") for asset in release.get("assets", [])}
    return AssetDiff(
        different=sorted(
            name for name in digests if name in remote and remote[name] != digests[name]
        ),
        missing=sorted(set(digests) - set(remote)),
        unexpected=sorted(set(remote) - set(digests)),
    )


def publish_github_release(
    version: str, tag: str, repo: str, dist: Path, notes: Path, *, gh: Gh
) -> None:
    """Create, complete or verify the GitHub release for ``tag``.

    Absent: create a draft, upload every file, publish. Draft: upload the
    missing files, clobber the different ones (nobody can download a draft
    yet), publish. Published: a no-op when every asset matches, else an error;
    a published release is never written to.
    """
    digests = {name: f"sha256:{_sha256(dist / name)}" for name in sorted(dist_files(dist))}
    repo_args = ["--repo", repo]
    existing = _read_release(tag, repo, gh)
    diff = _asset_diff(existing or {}, digests)
    if existing is not None and not existing["draft"]:
        if any(diff):
            listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in diff._asdict().items() if v)
            raise ReleaseError(f"release {tag} is already published with other assets: {listed}")
        print(f"release {tag} already published with these assets")
        return
    if diff.unexpected:
        raise ReleaseError(
            f"draft release {tag} has unexpected assets: {', '.join(diff.unexpected)}"
        )
    if existing is None:
        prerelease = ["--prerelease"] if Version(version).is_prerelease else []
        create = ["release", "create", tag, *repo_args, "--draft", "--verify-tag"]
        _checked(gh, *create, "--title", tag, "--notes-file", str(notes), *prerelease)
    upload = ["release", "upload", tag, *repo_args]
    if diff.missing:
        _checked(gh, *upload, *(str(dist / name) for name in diff.missing))
    if diff.different:
        _checked(gh, *upload, "--clobber", *(str(dist / name) for name in diff.different))
    _checked(gh, "release", "edit", tag, *repo_args, "--draft=false")


# --- CLI --------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    route_ = commands.add_parser("route", help="decide what a release run does")
    route_.add_argument("--event", required=True, help="github.event_name")
    route_.add_argument("--ref-type", required=True, help="github.ref_type")
    route_.add_argument("--ref-name", required=True, help="github.ref_name")
    route_.add_argument("--repository", required=True, help="github.repository (OWNER/REPO)")
    route_.add_argument("--before", default="", help="github.event.before (push to main only)")
    commands.add_parser("version", help="print the untaped package's version")
    readmes = commands.add_parser("readmes", help="point package README links at vVERSION")
    readmes.add_argument("version")
    check = commands.add_parser("check", help="check versions, pins and (with --dist) artifacts")
    check.add_argument("version", nargs="?", help="default: the untaped package's version")
    check.add_argument("--dist", type=Path)
    notes = commands.add_parser("notes", help="print the CHANGELOG section for VERSION")
    notes.add_argument("version")
    index = commands.add_parser("index", help="compare dist with the package index")
    index.add_argument("version")
    index.add_argument("--dist", type=Path, required=True)
    index.add_argument("--index", choices=sorted(INDEX_URLS), required=True)
    index.add_argument("--complete", action="store_true", help="wait until every file is there")
    smoke = commands.add_parser("smoke", help="smoke-test an installed untaped")
    smoke.add_argument("exe")
    smoke.add_argument("version")
    smoke.add_argument(
        "--expect",
        type=_names,
        help="comma-separated ready plugins (default: every first-party one; '' for none)",
    )
    smoke.add_argument(
        "--quarantined", type=_names, default=[], help="comma-separated quarantined plugins"
    )
    smoke.add_argument(
        "--agents",
        action="store_true",
        help="check each expected plugin's skill and every untaped agents page",
    )
    tag = commands.add_parser("tag", help="tag the checkout's commit, or confirm the tag is there")
    tag.add_argument("tag")
    tag.add_argument("--repo", required=True)
    github = commands.add_parser("github-release", help="create or verify the GitHub release")
    github.add_argument("version")
    github.add_argument("--tag", required=True)
    github.add_argument("--repo", required=True)
    github.add_argument("--dist", type=Path, required=True)
    github.add_argument("--notes", type=Path, required=True)
    return parser


def _check(root: Path, version: str, dist: Path | None) -> list[str]:
    found = packages(root)
    files = dist_files(dist) if dist is not None else set()
    errors = version_errors(found, version)
    if dist is not None:
        errors += artifact_errors(found, version, files)
        errors += main_link_errors(found, version, dist)
    if not errors:
        print(f"ok: {len(found)} package(s), {len(files)} artifact(s) for {version}")
    return errors


def _index(root: Path, args: argparse.Namespace) -> list[str]:
    errors, present, to_upload = index_errors(
        root, args.version, args.dist, index=args.index, complete=args.complete, fetch=fetch_json
    )
    if not errors:
        print(f"index ok: {present} present, {to_upload} to upload")
    return errors


def _smoke(root: Path, args: argparse.Namespace) -> list[str]:
    expect = plugin_names(root) if args.expect is None else args.expect
    errors, count = run_smoke(
        args.exe,
        args.version,
        expect=expect,
        quarantined=args.quarantined,
        agents=args.agents,
        guides=plugin_guides(root),
    )
    if not errors:
        print(f"smoke ok: untaped {args.version}, {count} plugins")
    return errors


def _dispatch(args: argparse.Namespace) -> list[str]:
    root: Path = args.root
    match args.command:
        case "route":
            decided = route(
                root,
                event=args.event,
                ref_type=args.ref_type,
                ref_name=args.ref_name,
                repository=args.repository,
                before=args.before,
            )
            print(decided.lines(), end="")
        case "version":
            print(release_version(root))
        case "readmes":
            changed = pin_readmes(root, args.version)
            print(f"pinned README links to v{args.version} in {changed} file(s)")
        case "check":
            version = release_version(root) if args.version is None else args.version
            return _check(root, version, args.dist)
        case "notes":
            print(release_notes(root / "CHANGELOG.md", args.version))
        case "index":
            return _index(root, args)
        case "smoke":
            return _smoke(root, args)
        case "tag":
            gh = functools.partial(_run, "gh")
            print(create_tag(args.tag, args.repo, head_commit(root), gh=gh))
        case "github-release":
            gh = functools.partial(_run, "gh")
            publish_github_release(args.version, args.tag, args.repo, args.dist, args.notes, gh=gh)
    return []


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; print each error on stderr and return 1 on failure."""
    args = _parser().parse_args(argv)
    try:
        errors = _dispatch(args)
    except ReleaseError as exc:
        errors = [str(exc)]
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
