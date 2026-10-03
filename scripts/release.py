"""Release checks for the tag-triggered workflow (``.github/workflows/release.yml``).

Usage: ``uv run python scripts/release.py [--root DIR] <command> ...``. Each
subcommand is one step of the workflow:

- ``version [--tag TAG]`` prints the ``untaped`` package's version (the
  version step, before the value reaches ``$GITHUB_ENV``); with ``--tag`` (a
  production release) it first checks that TAG is ``v<version>`` and that
  HEAD is on ``origin/main``, fetching ``main`` to know.
- ``check [VERSION] [--dist DIR]`` checks every package version and sibling
  pin against VERSION (default: the ``untaped`` package's version) before the
  build, and with ``--dist`` the built artifact list (after the build).
- ``notes VERSION`` prints the CHANGELOG section for the GitHub release body.
- ``index VERSION --dist DIR --index pypi|testpypi [--complete]`` compares the
  built files with what the index already holds: before publishing (conflicts
  fail, so a re-run uploads only what is missing) and, with ``--complete``,
  after publishing (waits until every file is there).
- ``smoke UNTAPED_EXE VERSION [--expect A,B] [--quarantined C] [--skills]``
  runs an installed ``untaped`` and checks its version, that its capabilities
  are exactly the expected ones ready (default: every first-party entry point)
  and the quarantined ones quarantined, every ``--help``, and with
  ``--skills`` each expected capability's packaged ``SKILL.md``.
- ``github-release VERSION --tag TAG --repo OWNER/REPO --dist DIR --notes FILE``
  creates, completes or verifies the GitHub release (the last job).

Every failure prints one line per error on stderr and exits 1.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from untaped.git import GitCommandError, git_toplevel, run_git

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FORMAT = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?$")
INDEX_URLS = {
    "pypi": "https://pypi.org/pypi/{name}/{version}/json",
    "testpypi": "https://test.pypi.org/pypi/{name}/{version}/json",
}
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


def packages(root: Path) -> dict[str, dict[str, Any]]:
    """``{canonical name: [project] table}`` for every package in the repo.

    The root ``[project]`` (if any) plus each directory matched by
    ``[tool.uv.workspace].members`` minus ``exclude`` that has a
    ``pyproject.toml`` with ``[project]``. Raises ``ReleaseError`` on a
    duplicate canonical name.
    """
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


def tagged_version(root: Path, tag: str) -> str:
    """``release_version``, after checking that ``tag`` is ``v<version>``."""
    version = release_version(root)
    if tag != f"v{version}":
        raise ReleaseError(f"tag {tag} does not match the package version {version}")
    return version


def check_on_main(root: Path) -> None:
    """Fetch ``origin``'s ``main`` and fail unless HEAD is on it (an ancestor or its tip)."""
    try:
        top = git_toplevel(root)
        if top is None:
            raise ReleaseError(f"{root} is not a git checkout")
        run_git(
            ["fetch", "--no-tags", "origin", "main"], cwd=top, timeout=120, retry_transient=True
        )
        head = run_git(["rev-parse", "HEAD"], cwd=top, timeout=30, capture=True).text.strip()
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


# --- artifacts --------------------------------------------------------------


def _package_artifacts(name: str, version: str) -> tuple[str, str]:
    stem = f"{name.replace('-', '_')}-{version}"
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
    """The stripped body of ``changelog``'s ``## <version>`` section."""
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
    return body


# --- smoke ------------------------------------------------------------------


def smoke_errors(
    version_out: str,
    capabilities_json: str,
    version: str,
    expected: Collection[str],
    quarantined: Collection[str] = (),
) -> list[str]:
    """The installed version, and exactly ``expected`` ready plus ``quarantined`` quarantined.

    A row for any other capability, a capability listed twice and a name in
    both sets are errors too.
    """
    errors = []
    if version_out.strip() != version:
        errors.append(f"untaped --version printed {version_out.strip()}, not {version}")
    try:
        rows = json.loads(capabilities_json)
        names = [row["name"] for row in rows]
        statuses = {row["name"]: row["status"] for row in rows}
    except ValueError, TypeError, KeyError:
        return [*errors, "untaped capabilities --format json did not print a list of rows"]
    both = set(expected) & set(quarantined)
    errors += [f"capability {name} is both expected and quarantined" for name in sorted(both)]
    errors += [f"capability {name} is listed twice" for name in _duplicates(names)]
    for status, wanted in (("ready", expected), ("quarantined", quarantined)):
        for name in sorted(set(wanted) - both):
            if name not in statuses:
                errors.append(f"capability {name} is missing")
            elif statuses[name] != status:
                unless = "" if status == "ready" else f", not {status}"
                errors.append(f"capability {name} is {statuses[name]}{unless}")
    unexpected = set(statuses) - set(expected) - set(quarantined)
    errors += [f"capability {name} is installed but not expected" for name in sorted(unexpected)]
    return errors


def _duplicates(names: Collection[str]) -> list[str]:
    return sorted(name for name, count in Counter(names).items() if count > 1)


def skill_errors(skills_json: str, expected: Collection[str]) -> list[str]:
    """The shell's ``untaped`` and ``untaped-<name>`` for each expected capability,
    each listed once with a SKILL.md."""
    try:
        rows = json.loads(skills_json)
        names = [row["name"] for row in rows]
        sources = {row["name"]: Path(row["source"]) for row in rows}
    except ValueError, TypeError, KeyError:
        return ["untaped skills list --format json did not print a list of rows"]
    errors = [f"skill {name} is listed twice" for name in _duplicates(names)]
    required = ["untaped", *(f"untaped-{name}" for name in sorted(expected))]
    errors += [f"skill {name} is missing" for name in required if name not in sources]
    errors += [
        f"skill {name} has no SKILL.md in {source}"
        for name, source in sorted(sources.items())
        if not (source / "SKILL.md").is_file()
    ]
    return errors


def capability_names(root: Path) -> list[str]:
    """Every capability the packages under ``root`` declare as an entry point."""
    return sorted(
        name
        for project in packages(root).values()
        for name in project.get("entry-points", {}).get("untaped.capabilities", {})
    )


def _run(exe: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([exe, *args], capture_output=True, text=True, check=False)


def run_smoke(
    exe: str,
    version: str,
    *,
    expect: Collection[str],
    quarantined: Collection[str] = (),
    skills: bool = False,
) -> tuple[list[str], int]:
    """Run the installed ``untaped``: (every smoke failure, expected capability count)."""
    rows = _run(exe, "capabilities", "--format", "json")
    version_out = _run(exe, "--version").stdout
    errors = smoke_errors(version_out, rows.stdout, version, expect, quarantined)
    if rows.returncode:
        errors.append(f"untaped capabilities --format json exited {rows.returncode}")
    for command in [[], *([name] for name in sorted(expect))]:
        code = _run(exe, *command, "--help").returncode
        if code:
            errors.append(f"untaped {' '.join([*command, '--help'])} exited {code}")
    if skills:
        listing = _run(exe, "skills", "list", "--format", "json")
        errors += skill_errors(listing.stdout, expect)
        if listing.returncode:
            errors.append(f"untaped skills list --format json exited {listing.returncode}")
    return errors, len(expect)


def _names(value: str) -> list[str]:
    """A comma-separated name list; ``""`` is none."""
    return [name.strip() for name in value.split(",") if name.strip()]


# --- GitHub release ---------------------------------------------------------


def _read_release(tag: str, repo: str, gh: Gh) -> dict[str, Any] | None:
    """The release for ``tag``, drafts included, or None when there is none.

    It lists releases because ``releases/tags/<tag>`` hides drafts. ``--jq '.[]'``
    prints one release per line on every page (``--slurp`` needs gh 2.48+).
    """
    result = gh("api", "--paginate", "--jq", ".[]", f"repos/{repo}/releases")
    if result.returncode:
        raise ReleaseError(f"could not list releases: {(result.stdout + result.stderr).strip()}")
    releases = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
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
    version = commands.add_parser("version", help="print the untaped package's version")
    version.add_argument("--tag", help="fail unless TAG is v<version> and HEAD is on main")
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
        help="comma-separated ready capabilities (default: every first-party one; '' for none)",
    )
    smoke.add_argument(
        "--quarantined", type=_names, default=[], help="comma-separated quarantined capabilities"
    )
    smoke.add_argument(
        "--skills", action="store_true", help="check each expected capability's packaged skill"
    )
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
    expect = capability_names(root) if args.expect is None else args.expect
    errors, count = run_smoke(
        args.exe, args.version, expect=expect, quarantined=args.quarantined, skills=args.skills
    )
    if not errors:
        print(f"smoke ok: untaped {args.version}, {count} capabilities")
    return errors


def _dispatch(args: argparse.Namespace) -> list[str]:
    root: Path = args.root
    match args.command:
        case "version" if args.tag is None:
            print(release_version(root))
        case "version":
            version = tagged_version(root, args.tag)
            check_on_main(root)
            print(version)
        case "check":
            version = release_version(root) if args.version is None else args.version
            return _check(root, version, args.dist)
        case "notes":
            print(release_notes(root / "CHANGELOG.md", args.version))
        case "index":
            return _index(root, args)
        case "smoke":
            return _smoke(root, args)
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
