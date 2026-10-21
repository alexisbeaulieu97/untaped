"""scripts/release.py: discovery, versions and pins, routing, artifacts, index, notes, smoke,
tag and GitHub release."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import urllib.error
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import release

from repo.support import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "release.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _project(
    dir_: Path,
    name: str,
    version: str,
    deps: tuple[str, ...] = (),
    extras: dict[str, list[str]] | None = None,
) -> None:
    lines = [
        "[project]",
        f'name = "{name}"',
        f'version = "{version}"',
        f"dependencies = {list(deps)!r}",
    ]
    if extras:
        lines.append("[project.optional-dependencies]")
        lines += [f"{key} = {value!r}" for key, value in extras.items()]
    _write(dir_ / "pyproject.toml", "\n".join(lines) + "\n")


PLUGINS = ("awx", "ansible", "github", "jira", "workspace", "recipe")


def _split_repo(root: Path, version: str = "10.0.0") -> Path:
    """The layout: a virtual root, core and six plugin packages, one excluded example."""
    _write(
        root / "pyproject.toml",
        "[tool.uv.workspace]\n"
        'members = ["packages/*", "examples/*"]\n'
        'exclude = ["examples/untaped-hello"]\n',
    )
    pins = {name: [f"untaped-{name}=={version}"] for name in PLUGINS}
    _project(
        root / "packages" / "untaped",
        "untaped",
        version,
        ("pydantic>=2",),
        {**pins, "all": [f"untaped-{c}=={version}" for c in PLUGINS]},
    )
    for name in PLUGINS:
        deps = (f"untaped=={version}",) + (
            (f"untaped-github=={version}",) if name in ("ansible", "workspace") else ()
        )
        _project(root / "packages" / f"untaped-{name}", f"untaped-{name}", version, deps)
    _project(root / "examples" / "untaped-hello", "untaped-hello", "0.1.0", ("untaped>=10",))
    return root


# --- discovery and versions -------------------------------------------------


def test_a_single_project_repo_is_one_package(tmp_path: Path) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert list(release.packages(tmp_path)) == ["untaped"]
    assert release.release_version(tmp_path) == "10.0.0"


def test_the_split_layout_finds_seven_packages_and_skips_the_example(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    assert sorted(release.packages(root)) == sorted(["untaped", *(f"untaped-{c}" for c in PLUGINS)])
    assert release.release_version(root) == "10.0.0"
    assert release.version_errors(release.packages(root), "10.0.0") == []


def test_duplicate_package_names_are_refused(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    _project(root / "packages" / "copy", "Untaped_AWX", "10.0.0")
    with pytest.raises(
        release.ReleaseError,
        match=r"^two packages are named untaped-awx: packages/copy, packages/untaped-awx$",
    ):
        release.packages(root)


def test_a_repo_without_the_untaped_package_has_no_release_version(tmp_path: Path) -> None:
    _project(tmp_path, "other", "1.0.0")
    with pytest.raises(
        release.ReleaseError, match=f"^no package named untaped under {re.escape(str(tmp_path))}$"
    ):
        release.release_version(tmp_path)


@pytest.mark.parametrize(
    "version", ["10.0", "v10.0.0", "10.0.0.dev1", "10.0.0+local", "10.0.0-rc1"]
)
def test_a_malformed_release_version_is_refused(tmp_path: Path, version: str) -> None:
    _project(tmp_path, "untaped", version)
    message = f"version {version} is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN"
    with pytest.raises(release.ReleaseError, match=f"^{re.escape(message)}$"):
        release.release_version(tmp_path)


@pytest.mark.parametrize("version", ["10.0.0", "10.0.0a1", "10.0.0b2", "10.0.0rc3"])
def test_release_versions_accept_finals_and_prereleases(tmp_path: Path, version: str) -> None:
    _project(tmp_path, "untaped", version)
    assert release.release_version(tmp_path) == version


def test_version_errors_report_a_malformed_tag_first(tmp_path: Path) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert release.version_errors(release.packages(tmp_path), "v10.0.0") == [
        "version v10.0.0 is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN",
        "untaped is at 10.0.0, not v10.0.0",
    ]


def test_a_trailing_newline_is_not_a_release_version(tmp_path: Path) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert release.version_errors(release.packages(tmp_path), "10.0.0\n") == [
        "version 10.0.0\n is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN",
        "untaped is at 10.0.0, not 10.0.0\n",
    ]


def test_a_tag_that_differs_names_every_package_and_pin(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "untaped",
        "10.0.0",
        ("pydantic>=2",),
        {"awx": ["untaped-awx==10.0.0"], "all": ["untaped-awx==10.0.0"]},
    )
    _write(
        tmp_path / "pyproject.toml",
        (tmp_path / "pyproject.toml").read_text()
        + '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
    )
    _project(tmp_path / "packages" / "untaped-awx", "untaped-awx", "10.0.0", ("untaped==10.0.0",))
    assert release.version_errors(release.packages(tmp_path), "10.0.1") == [
        "untaped is at 10.0.0, not 10.0.1",
        "untaped pins untaped-awx==10.0.0, not ==10.0.1",
        "untaped pins untaped-awx==10.0.0, not ==10.0.1",
        "untaped-awx is at 10.0.0, not 10.0.1",
        "untaped-awx pins untaped==10.0.0, not ==10.0.1",
    ]


@pytest.mark.parametrize(
    "requirement",
    [
        "untaped>=10",
        "untaped===10.0.0",
        "untaped==10.*",
        "untaped>=10,<11",
        "untaped @ https://example.invalid/untaped.whl",
    ],
)
def test_only_an_exact_pin_satisfies_a_sibling(tmp_path: Path, requirement: str) -> None:
    root = _split_repo(tmp_path)
    _project(
        root / "packages" / "untaped-jira", "untaped-jira", "10.0.0", (requirement, "httpx>=0.27")
    )
    assert release.version_errors(release.packages(root), "10.0.0") == [
        f"untaped-jira pins {requirement}, not ==10.0.0"
    ]


def test_markers_and_extras_on_an_exact_pin_are_fine(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    _project(
        root / "packages" / "untaped-jira",
        "untaped-jira",
        "10.0.0",
        ('untaped[tui]==10.0.0; python_version >= "3.14"',),
    )
    assert release.version_errors(release.packages(root), "10.0.0") == []


# --- artifacts --------------------------------------------------------------


def test_expected_artifacts_normalize_names(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    stems = ["untaped", *(f"untaped_{name}" for name in PLUGINS)]
    assert release.expected_artifacts(release.packages(root), "10.0.0") == {
        file
        for stem in stems
        for file in (f"{stem}-10.0.0-py3-none-any.whl", f"{stem}-10.0.0.tar.gz")
    }


def test_artifact_errors_name_missing_and_stray_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root, "untaped", "10.0.0")
    dist = tmp_path / "dist"
    for name in ("untaped-10.0.0-py3-none-any.whl", "untaped-9.1.0.tar.gz", ".gitignore"):
        _write(dist / name, "")
    assert release.artifact_errors(release.packages(root), "10.0.0", release.dist_files(dist)) == [
        "missing: untaped-10.0.0.tar.gz",
        "unexpected: .gitignore",
        "unexpected: untaped-9.1.0.tar.gz",
    ]


# --- README links -----------------------------------------------------------

DOCS = "https://github.com/alexisbeaulieu97/untaped"
PINNED = f"See [docs]({DOCS}/blob/v10.0.0/docs/a.md)."


def _built(dist: Path, stem: str, description: str) -> None:
    """A wheel and an sdist for ``stem`` whose core metadata carries ``description``."""
    metadata = f"Metadata-Version: 2.4\nName: x\n\n{description}\n".encode()
    dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dist / f"{stem}-py3-none-any.whl", "w") as wheel:
        wheel.writestr(f"{stem}.dist-info/METADATA", metadata)
    with tarfile.open(dist / f"{stem}.tar.gz", "w:gz") as sdist:
        info = tarfile.TarInfo(f"{stem}/PKG-INFO")
        info.size = len(metadata)
        sdist.addfile(info, io.BytesIO(metadata))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"[a]({DOCS}/blob/main/docs/a.md#x)", f"[a]({DOCS}/blob/v10.1.0/docs/a.md#x)"),
        (f"[a]({DOCS}/tree/main/docs)", f"[a]({DOCS}/tree/v10.1.0/docs)"),
        (f"[a]({DOCS}/tree/main)", f"[a]({DOCS}/tree/v10.1.0)"),
        (f"{DOCS}/blob/main/a {DOCS}/blob/main/b", f"{DOCS}/blob/v10.1.0/a {DOCS}/blob/v10.1.0/b"),
        (f"[a]({DOCS}#readme)", f"[a]({DOCS}#readme)"),
        (f"[a]({DOCS}/blob/mainline/a.md)", f"[a]({DOCS}/blob/mainline/a.md)"),
        (f"[a]({DOCS}/blob/main-old/a.md)", f"[a]({DOCS}/blob/main-old/a.md)"),
        ("[a](https://github.com/other/repo/blob/main/a.md)", None),
    ],
    ids=[
        "blob",
        "tree",
        "tree-root",
        "every-link",
        "repo-root",
        "mainline",
        "main-old",
        "other-repo",
    ],
)
def test_pin_links_points_this_repositorys_main_links_at_the_tag(
    text: str, expected: str | None
) -> None:
    assert release.pin_links(text, "10.1.0") == (text if expected is None else expected)


def test_main_link_errors_read_each_built_page(tmp_path: Path) -> None:
    root = _split_repo(tmp_path / "repo")
    dist = tmp_path / "dist"
    _built(dist, "untaped-10.0.0", PINNED)
    _built(dist, "untaped_awx-10.0.0", f"[a]({DOCS}/tree/main/docs)")
    _write(dist / "untaped_jira-10.0.0-py3-none-any.whl", "not a zip")
    _write(dist / "untaped_jira-10.0.0.tar.gz", "not a tarball")
    assert release.main_link_errors(release.packages(root), "10.0.0", dist) == [
        f"untaped_awx-10.0.0-py3-none-any.whl links to main: {DOCS}/tree/main",
        f"untaped_awx-10.0.0.tar.gz links to main: {DOCS}/tree/main",
        "untaped_jira-10.0.0-py3-none-any.whl: cannot read its metadata: File is not a zip file",
        "untaped_jira-10.0.0.tar.gz: cannot read its metadata:"
        " file could not be opened successfully:",
    ]


def test_main_link_errors_need_the_metadata_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root, "untaped", "10.0.0")
    dist = tmp_path / "dist"
    _built(dist, "untaped-9.0.0", PINNED)
    for old, new in (
        ("9.0.0-py3-none-any.whl", "10.0.0-py3-none-any.whl"),
        ("9.0.0.tar.gz", "10.0.0.tar.gz"),
    ):
        (dist / f"untaped-{old}").rename(dist / f"untaped-{new}")
    assert release.main_link_errors(release.packages(root), "10.0.0", dist) == [
        "untaped-10.0.0-py3-none-any.whl: cannot read its metadata:"
        " \"There is no item named 'untaped-10.0.0.dist-info/METADATA' in the archive\"",
        "untaped-10.0.0.tar.gz: cannot read its metadata:"
        " \"filename 'untaped-10.0.0/PKG-INFO' not found\"",
    ]


# --- index ------------------------------------------------------------------

WHEEL = "untaped-10.0.0-py3-none-any.whl"
SDIST = "untaped-10.0.0.tar.gz"
TESTPYPI_URL = "https://test.pypi.org/pypi/untaped/10.0.0/json"


def _dist(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    _project(root, "untaped", "10.0.0")
    dist = tmp_path / "dist"
    _write(dist / WHEEL, "wheel")
    _write(dist / SDIST, "sdist")
    return root, dist


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


CONFLICT = f"conflict: {WHEEL} on testpypi has sha256 {_sha('other')}, local {_sha('wheel')}"


def _payload(files: dict[str, str]) -> dict[str, Any]:
    return {
        "urls": [
            {"filename": name, "digests": {"sha256": _sha(body)}} for name, body in files.items()
        ]
    }


def _fetch(files: dict[str, str] | None) -> Callable[[str], dict[str, Any] | None]:
    def fetch(url: str) -> dict[str, Any] | None:
        assert url == TESTPYPI_URL
        return None if files is None else _payload(files)

    return fetch


@pytest.mark.parametrize(
    ("remote", "complete", "expected"),
    [
        (None, False, ([], 0, 2)),
        ({WHEEL: "wheel"}, False, ([], 1, 1)),
        ({WHEEL: "wheel", SDIST: "sdist"}, True, ([], 2, 0)),
        ({WHEEL: "wheel"}, True, ([f"missing on testpypi: {SDIST}"], 1, 1)),
        (
            {WHEEL: "other"},
            False,
            (
                [CONFLICT],
                1,
                1,
            ),
        ),
        (
            {"untaped-10.0.0-cp314-cp314-linux_x86_64.whl": "x"},
            False,
            (["unexpected on testpypi: untaped-10.0.0-cp314-cp314-linux_x86_64.whl"], 0, 2),
        ),
    ],
    ids=[
        "nothing-published",
        "partial",
        "complete",
        "complete-required-but-missing",
        "conflict",
        "foreign-file",
    ],
)
def test_index_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: dict[str, str] | None,
    complete: bool,
    expected: tuple[list[str], int, int],
) -> None:
    monkeypatch.setattr(release, "INDEX_TRIES", 1)
    root, dist = _dist(tmp_path)
    result = release.index_errors(
        root, "10.0.0", dist, index="testpypi", complete=complete, fetch=_fetch(remote)
    )
    assert result == expected


def test_the_pypi_index_uses_the_pypi_url(tmp_path: Path) -> None:
    root, dist = _dist(tmp_path)
    urls: list[str] = []

    def fetch(url: str) -> dict[str, Any] | None:
        urls.append(url)
        return None

    release.index_errors(root, "10.0.0", dist, index="pypi", complete=False, fetch=fetch)
    assert urls == ["https://pypi.org/pypi/untaped/10.0.0/json"]


def _sequence(*payloads: dict[str, Any] | None) -> Callable[[str], dict[str, Any] | None]:
    remaining = list(payloads)

    def fetch(url: str) -> dict[str, Any] | None:
        assert url == TESTPYPI_URL
        return remaining.pop(0)

    return fetch


def test_a_complete_check_waits_for_the_index_to_catch_up(tmp_path: Path) -> None:
    root, dist = _dist(tmp_path)
    delays: list[float] = []
    fetch = _sequence(None, _payload({WHEEL: "wheel"}), _payload({WHEEL: "wheel", SDIST: "sdist"}))
    result = release.index_errors(
        root, "10.0.0", dist, index="testpypi", complete=True, fetch=fetch, sleep=delays.append
    )
    assert result == ([], 2, 0)
    assert delays == [10, 10]


def test_a_conflict_fails_the_wait_without_sleeping(tmp_path: Path) -> None:
    root, dist = _dist(tmp_path)
    delays: list[float] = []
    fetch = _sequence(_payload({WHEEL: "other"}))
    errors, _, _ = release.index_errors(
        root, "10.0.0", dist, index="testpypi", complete=True, fetch=fetch, sleep=delays.append
    )
    assert errors == [
        CONFLICT,
        f"missing on testpypi: {SDIST}",
    ]
    assert delays == []


def test_the_wait_gives_up_after_twelve_tries(tmp_path: Path) -> None:
    root, dist = _dist(tmp_path)
    delays: list[float] = []
    calls: list[str] = []

    def fetch(url: str) -> dict[str, Any] | None:
        calls.append(url)
        return None

    errors, present, to_upload = release.index_errors(
        root, "10.0.0", dist, index="testpypi", complete=True, fetch=fetch, sleep=delays.append
    )
    assert errors == [f"missing on testpypi: {WHEEL}", f"missing on testpypi: {SDIST}"]
    assert (present, to_upload, len(calls), delays) == (0, 2, 12, [10] * 11)


def test_a_wait_stops_on_a_fetch_error_after_one_sleep(tmp_path: Path) -> None:
    root, dist = _dist(tmp_path)
    delays: list[float] = []
    payloads: list[dict[str, Any] | None] = [None]

    def fetch(url: str) -> dict[str, Any] | None:
        if not payloads:
            raise release.ReleaseError("could not read the index")
        return payloads.pop()

    with pytest.raises(release.ReleaseError, match=r"^could not read the index$"):
        release.index_errors(
            root, "10.0.0", dist, index="testpypi", complete=True, fetch=fetch, sleep=delays.append
        )
    assert delays == [10]


def test_a_wait_re_fetches_only_the_packages_still_missing_files(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    dist = tmp_path / "dist"
    for file in release.expected_artifacts(release.packages(root), "10.0.0"):
        _write(dist / file, file)
    urls: list[str] = []

    def fetch(url: str) -> dict[str, Any] | None:
        urls.append(url)
        name = url.split("/")[-3]
        stem = name.replace("-", "_")
        files = [f"{stem}-10.0.0-py3-none-any.whl", f"{stem}-10.0.0.tar.gz"]
        if name == "untaped" and urls.count(url) == 1:
            files = files[:1]
        return _payload({file: file for file in files})

    errors, present, to_upload = release.index_errors(
        root, "10.0.0", dist, index="testpypi", complete=True, fetch=fetch, sleep=lambda _: None
    )
    names = sorted(["untaped", *(f"untaped-{name}" for name in PLUGINS)])
    first = [f"https://test.pypi.org/pypi/{name}/10.0.0/json" for name in names]
    assert urls == [*first, "https://test.pypi.org/pypi/untaped/10.0.0/json"]
    assert (errors, present, to_upload) == ([], 2 * len(names), 0)


def test_each_package_is_checked_against_its_own_index_entry(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    dist = tmp_path / "dist"
    for file in release.expected_artifacts(release.packages(root), "10.0.0"):
        _write(dist / file, file)
    urls: list[str] = []
    awx_url = "https://test.pypi.org/pypi/untaped-awx/10.0.0/json"
    awx_wheel = "untaped_awx-10.0.0-py3-none-any.whl"

    def fetch(url: str) -> dict[str, Any] | None:
        urls.append(url)
        if url == awx_url:
            # The core wheel listed under untaped-awx is foreign there.
            return _payload({awx_wheel: awx_wheel, WHEEL: WHEEL})
        return None

    errors, present, to_upload = release.index_errors(
        root, "10.0.0", dist, index="testpypi", complete=False, fetch=fetch
    )
    names = sorted(["untaped", *(f"untaped-{name}" for name in PLUGINS)])
    assert urls == [f"https://test.pypi.org/pypi/{name}/10.0.0/json" for name in names]
    assert errors == [f"unexpected on testpypi: {WHEEL}"]
    assert (present, to_upload) == (1, 2 * len(names) - 1)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(TESTPYPI_URL, code, "error", None, None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(500), f"could not read {TESTPYPI_URL}: HTTP 500"),
        (
            urllib.error.URLError("timed out"),
            f"could not read {TESTPYPI_URL}: <urlopen error timed out>",
        ),
    ],
    ids=["http-500", "timeout"],
)
def test_fetch_json_fails_on_anything_but_a_404(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    def urlopen(url: str, timeout: float) -> None:
        raise error

    monkeypatch.setattr(release.urllib.request, "urlopen", urlopen)
    with pytest.raises(release.ReleaseError) as caught:
        release.fetch_json(TESTPYPI_URL)
    assert str(caught.value) == expected


def test_fetch_json_returns_none_on_a_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(url: str, timeout: float) -> None:
        assert (url, timeout) == (TESTPYPI_URL, 30)
        raise _http_error(404)

    monkeypatch.setattr(release.urllib.request, "urlopen", urlopen)
    assert release.fetch_json(TESTPYPI_URL) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            b"<html>",
            f"{TESTPYPI_URL} did not return JSON: Expecting value: line 1 column 1 (char 0)",
        ),
        (b"[]", f"{TESTPYPI_URL} did not return a JSON object"),
    ],
    ids=["not-json", "not-an-object"],
)
def test_fetch_json_refuses_a_body_that_is_not_a_json_object(
    monkeypatch: pytest.MonkeyPatch, body: bytes, expected: str
) -> None:
    def urlopen(url: str, timeout: float) -> io.BytesIO:
        return io.BytesIO(body)

    monkeypatch.setattr(release.urllib.request, "urlopen", urlopen)
    with pytest.raises(release.ReleaseError) as caught:
        release.fetch_json(TESTPYPI_URL)
    assert str(caught.value) == expected


@pytest.mark.parametrize(
    "payload",
    [{}, {"urls": [{"filename": WHEEL}]}, {"urls": None}],
    ids=["no-urls", "no-digests", "urls-null"],
)
def test_an_index_payload_without_a_file_list_is_refused(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    root, dist = _dist(tmp_path)
    with pytest.raises(release.ReleaseError) as caught:
        release.index_errors(
            root, "10.0.0", dist, index="testpypi", complete=False, fetch=lambda url: payload
        )
    assert str(caught.value) == f"{TESTPYPI_URL} has no file list (urls[].filename, digests.sha256)"


# --- notes ------------------------------------------------------------------

CHANGELOG = (
    "# Changelog\n\n## Unreleased\n\n- next\n\n## 10.0.0\n\n- Core\n  - **New:** x\n\n"
    "## 9.1.0\n\n- old\n"
)


def test_release_notes_point_main_links_at_the_tag(tmp_path: Path) -> None:
    _write(
        tmp_path / "CHANGELOG.md",
        f"# Changelog\n\n## 10.1.0\n\n- See [docs]({DOCS}/blob/main/docs/a.md).\n",
    )
    assert release.release_notes(tmp_path / "CHANGELOG.md", "10.1.0") == (
        f"- See [docs]({DOCS}/blob/v10.1.0/docs/a.md)."
    )


def test_release_notes_are_the_versions_section(tmp_path: Path) -> None:
    _write(tmp_path / "CHANGELOG.md", CHANGELOG)
    assert release.release_notes(tmp_path / "CHANGELOG.md", "10.0.0") == "- Core\n  - **New:** x"


def test_release_notes_keep_subsections(tmp_path: Path) -> None:
    _write(
        tmp_path / "CHANGELOG.md",
        "# Changelog\n\n## 1.2.3\n\n### Added\n\n- a\n\n### Changed\n\n- b\n\n## 1.2.2\n\n- old\n",
    )
    assert release.release_notes(tmp_path / "CHANGELOG.md", "1.2.3") == (
        "### Added\n\n- a\n\n### Changed\n\n- b"
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# Changelog\n\n## 9.1.0\n\n- old\n", 'CHANGELOG.md has no "## 10.0.0" section'),
        (
            "# Changelog\n\n## 10.0.0\n\n## 9.1.0\n\n- old\n",
            'CHANGELOG.md\'s "## 10.0.0" section is empty',
        ),
        ("# Changelog\n\n## 10.0.0rc1\n\n- rc\n", 'CHANGELOG.md has no "## 10.0.0" section'),
    ],
)
def test_release_notes_fail_without_a_section(tmp_path: Path, text: str, message: str) -> None:
    _write(tmp_path / "CHANGELOG.md", text)
    with pytest.raises(release.ReleaseError, match=f"^{re.escape(message)}$"):
        release.release_notes(tmp_path / "CHANGELOG.md", "10.0.0")


# --- smoke ------------------------------------------------------------------

ROWS = [
    {"name": "awx", "status": "ready"},
    {"name": "jira", "status": "quarantined"},
    {"name": "extra", "status": "ready"},
]


def test_smoke_errors_report_version_missing_and_unready() -> None:
    assert release.smoke_errors(
        "9.9.9\n", json.dumps(ROWS), "10.0.0", ["awx", "jira", "github"]
    ) == [
        "untaped --version printed 9.9.9, not 10.0.0",
        "plugin github is missing",
        "plugin jira is quarantined",
        "plugin extra is installed but not expected",
    ]
    assert release.smoke_errors("10.0.0\n", json.dumps(ROWS[:1]), "10.0.0", ["awx"]) == []


def test_smoke_errors_refuse_output_that_is_not_a_list_of_rows() -> None:
    assert release.smoke_errors("10.0.0", "not json", "10.0.0", ["awx"]) == [
        "untaped plugin list --format json did not print a list of rows"
    ]


def test_smoke_errors_refuse_an_unexpected_plugin() -> None:
    rows = [{"name": "awx", "status": "ready"}, {"name": "github", "status": "ready"}]
    assert release.smoke_errors("10.0.0a0", json.dumps(rows), "10.0.0a0", ["awx"]) == [
        "plugin github is installed but not expected"
    ]


def test_smoke_errors_check_quarantined_names() -> None:
    rows = [{"name": "hello", "status": "ready"}]
    assert release.smoke_errors(
        "10.0.0a0", json.dumps(rows), "10.0.0a0", [], quarantined=["hello"]
    ) == ["plugin hello is ready, not quarantined"]
    rows = [{"name": "hello", "status": "quarantined"}]
    assert (
        release.smoke_errors("10.0.0a0", json.dumps(rows), "10.0.0a0", [], quarantined=["hello"])
        == []
    )
    assert release.smoke_errors("10.0.0a0", "[]", "10.0.0a0", [], quarantined=["hello"]) == [
        "plugin hello is missing"
    ]


def test_smoke_errors_refuse_duplicates_and_overlap() -> None:
    rows = [{"name": "awx", "status": "ready"}, {"name": "awx", "status": "ready"}]
    assert release.smoke_errors(
        "1.0.0", json.dumps(rows), "1.0.0", ["awx"], quarantined=["awx"]
    ) == [
        "plugin awx is both expected and quarantined",
        "plugin awx is listed twice",
    ]


def test_plugin_names_are_the_packages_entry_points(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "untaped"\nversion = "10.0.0"\n'
        '[project.entry-points."untaped.plugins"]\n'
        'zeta = "z:SPEC"\nalpha = "a:SPEC"\n',
    )
    assert release.plugin_names(tmp_path) == ["alpha", "zeta"]


def test_plugin_names_span_workspace_members(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    _write(
        root / "packages" / "untaped-github" / "pyproject.toml",
        '[project]\nname = "untaped-github"\nversion = "10.0.0"\n'
        '[project.entry-points."untaped.plugins"]\ngithub = "g:SPEC"\n',
    )
    _write(
        root / "packages" / "untaped-awx" / "pyproject.toml",
        '[project]\nname = "untaped-awx"\nversion = "10.0.0"\n'
        '[project.entry-points."untaped.plugins"]\nawx = "a:SPEC"\n',
    )
    assert release.plugin_names(root) == ["awx", "github"]


FIRST_PARTY = release.plugin_names(release.REPO_ROOT)
FAKE_UNTAPED = """#!/bin/sh
case "$1" in
  --version) echo "$FAKE_VERSION" ;;
  plugin) echo "$FAKE_ROWS"; exit "${FAKE_ROWS_EXIT:-0}" ;;
  skills) echo "$FAKE_SKILLS"; exit "${FAKE_SKILLS_EXIT:-0}" ;;
  agents)
    [ "$3" = "$FAKE_MISSING_GUIDE" ] && [ -n "$3" ] && exit 2
    [ "$2" = "$FAKE_EMPTY_PAGE" ] && [ -n "$2" ] && exit 0
    printf '# page\n\n## Guides\n- `writes`: Read before a write.\n' ;;
  "$FAKE_FAILING") exit 2 ;;
esac
exit 0
"""


def _fake_untaped(tmp_path: Path) -> Path:
    exe = tmp_path / "untaped"
    _write(exe, FAKE_UNTAPED)
    exe.chmod(0o755)
    return exe


READY = [{"name": name, "status": "ready"} for name in FIRST_PARTY]


def _smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rows: list[dict[str, str]],
    failing: str = "",
    rows_exit: int = 0,
) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of ``smoke`` against the fake untaped."""
    monkeypatch.setenv("FAKE_VERSION", "10.0.0")
    monkeypatch.setenv("FAKE_ROWS", json.dumps(rows))
    monkeypatch.setenv("FAKE_ROWS_EXIT", str(rows_exit))
    monkeypatch.setenv("FAKE_FAILING", failing)
    code = release.main(["smoke", str(_fake_untaped(tmp_path)), "10.0.0"])
    out, err = capsys.readouterr()
    return code, out, err


def test_the_smoke_command_passes_a_healthy_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _smoke(tmp_path, monkeypatch, capsys, READY) == (
        0,
        f"smoke ok: untaped 10.0.0, {len(FIRST_PARTY)} plugins\n",
        "",
    )


@pytest.mark.parametrize(
    ("rows", "failing", "rows_exit", "stderr"),
    [
        (READY, FIRST_PARTY[-1], 0, f"untaped {FIRST_PARTY[-1]} --help exited 2\n"),
        (READY, "--help", 0, "untaped --help exited 2\n"),
        (READY, "", 3, "untaped plugin list --format json exited 3\n"),
        (
            [{"name": FIRST_PARTY[0], "status": "quarantined"}, *READY[1:]],
            "",
            0,
            f"plugin {FIRST_PARTY[0]} is quarantined\n",
        ),
    ],
    ids=["plugin-help", "root-help", "plugin-list", "not-ready"],
)
def test_the_smoke_command_reports_each_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rows: list[dict[str, str]],
    failing: str,
    rows_exit: int,
    stderr: str,
) -> None:
    assert _smoke(tmp_path, monkeypatch, capsys, rows, failing, rows_exit) == (1, "", stderr)


def test_smoke_cli_expects_nothing_for_a_bare_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exe = _fake_untaped(tmp_path)
    monkeypatch.setenv("FAKE_VERSION", "10.0.0a0")
    monkeypatch.setenv("FAKE_ROWS", "[]")
    assert release.main(["smoke", str(exe), "10.0.0a0", "--expect", ""]) == 0
    assert "smoke ok: untaped 10.0.0a0, 0 plugins" in capsys.readouterr().out


def test_smoke_cli_checks_expected_and_quarantined_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exe = _fake_untaped(tmp_path)
    rows = [{"name": "awx", "status": "ready"}, {"name": "hello", "status": "quarantined"}]
    monkeypatch.setenv("FAKE_VERSION", "10.0.0a0")
    monkeypatch.setenv("FAKE_ROWS", json.dumps(rows))
    argv = ["smoke", str(exe), "10.0.0a0", "--expect", "awx", "--quarantined", "hello"]
    assert release.main(argv) == 0
    assert capsys.readouterr().out == "smoke ok: untaped 10.0.0a0, 1 plugins\n"
    assert release.main(["smoke", str(exe), "10.0.0a0", "--expect", "awx,jira"]) == 1
    assert capsys.readouterr().err == (
        "plugin jira is missing\nplugin hello is installed but not expected\n"
    )


def _skill(name: str) -> dict[str, str]:
    return {"name": name, "description": "d", "source": "untaped:stub/1", "kind": "stub"}


def test_smoke_agents_need_each_expected_skill_and_every_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exe = _fake_untaped(tmp_path)
    rows = [{"name": "awx", "status": "ready"}, {"name": "jira", "status": "ready"}]
    monkeypatch.setenv("FAKE_VERSION", "10.0.0a0")
    monkeypatch.setenv("FAKE_ROWS", json.dumps(rows))
    monkeypatch.setenv("FAKE_SKILLS", json.dumps([_skill("untaped"), _skill("untaped-awx")]))
    monkeypatch.setenv("FAKE_MISSING_GUIDE", "writes")
    monkeypatch.setenv("FAKE_EMPTY_PAGE", "jira")
    argv = ["smoke", str(exe), "10.0.0a0", "--expect", "awx,jira", "--agents"]
    assert release.main(argv) == 1
    assert capsys.readouterr().err == (
        "skill untaped-jira is missing\n"
        "untaped agents untaped writes exited 2\n"
        "untaped agents awx writes exited 2\n"
        "untaped agents jira printed nothing\n"
    )
    monkeypatch.setenv("FAKE_ROWS", json.dumps(rows[:1]))
    monkeypatch.setenv("FAKE_MISSING_GUIDE", "")
    assert release.main(["smoke", str(exe), "10.0.0a0", "--expect", "awx", "--agents"]) == 0
    assert capsys.readouterr().out == "smoke ok: untaped 10.0.0a0, 1 plugins\n"


def test_page_guides_reads_only_the_guides_section() -> None:
    page = (
        "# awx\n\n## Commands\n- `job list`: List jobs.\n\n"
        "## Guides\n- `writes`: Read before a write.\n- `test-cases`: Read when...\n\n"
        "## Settings\n- `token`: The token.\n"
    )
    assert release.page_guides(page) == ["writes", "test-cases"]
    assert release.page_guides("# lint\n\n## Commands\n- `lint`: Lint.\n") == []


def test_skill_errors_refuse_a_skill_listed_twice() -> None:
    shell, skill = _skill("untaped"), _skill("untaped-awx")
    assert release.skill_errors(json.dumps([shell, skill, skill]), ["awx"]) == [
        "skill untaped-awx is listed twice"
    ]


def test_skill_errors_need_the_shells_own_skill() -> None:
    skill = _skill("untaped-awx")
    assert release.skill_errors(json.dumps([skill]), ["awx"]) == ["skill untaped is missing"]


@pytest.mark.parametrize(
    ("skills", "exit_code", "error"),
    [
        ("not json", "0", "untaped skills list --format json did not print a list of rows"),
        ("[]", "4", "untaped skills list --format json exited 4"),
    ],
)
def test_smoke_agents_report_a_broken_skill_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    skills: str,
    exit_code: str,
    error: str,
) -> None:
    exe = _fake_untaped(tmp_path)
    monkeypatch.setenv("FAKE_VERSION", "10.0.0a0")
    monkeypatch.setenv("FAKE_ROWS", "[]")
    monkeypatch.setenv("FAKE_SKILLS", skills)
    monkeypatch.setenv("FAKE_SKILLS_EXIT", exit_code)
    assert release.main(["smoke", str(exe), "10.0.0a0", "--expect", "", "--agents"]) == 1
    assert capsys.readouterr().err.endswith(f"{error}\n")


# --- GitHub release ---------------------------------------------------------

REPO = "owner/untaped"
TAG = "v10.0.0"
LIST = ["api", "--paginate", "--jq", ".[]", f"repos/{REPO}/releases"]
PUBLISH = ["release", "edit", TAG, "--repo", REPO, "--draft=false"]


class FakeGh:
    """A fake ``gh`` over an in-memory release list.

    ``releases`` rows are ``{"tag_name", "draft", "assets": [{"name", "digest"}]}``;
    the list endpoint returns them as JSON lines (drafts included, as GitHub
    does). Writes update the list. ``fail`` makes the first call whose verb
    (``create``/``upload``/``edit``) matches exit 1, and ``list_error`` makes
    the list call fail with that stderr.
    """

    def __init__(
        self,
        releases: list[dict[str, Any]] | None = None,
        *,
        fail: str = "",
        list_error: str = "",
    ) -> None:
        self.releases = releases or []
        self.fail = fail
        self.list_error = list_error
        self.calls: list[list[str]] = []

    def __call__(self, *argv: str) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        if args == LIST:
            if self.list_error:
                return subprocess.CompletedProcess(args, 1, "", self.list_error)
            lines = "".join(json.dumps(row) + "\n" for row in self.releases)
            return subprocess.CompletedProcess(args, 0, lines, "")
        verb = args[1]
        if verb == self.fail:
            self.fail = ""
            return subprocess.CompletedProcess(args, 1, "", f"{verb} failed")
        self._apply(verb, args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def _apply(self, verb: str, args: list[str]) -> None:
        if verb == "create":
            self.releases.append({"tag_name": args[2], "draft": True, "assets": []})
            return
        (row,) = [row for row in self.releases if row["tag_name"] == args[2]]
        if verb == "edit":
            row["draft"] = False
            return
        for path in (Path(arg) for arg in args[5:] if arg != "--clobber"):
            row["assets"] = [a for a in row["assets"] if a["name"] != path.name]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            row["assets"].append({"name": path.name, "digest": f"sha256:{digest}"})

    @property
    def writes(self) -> list[list[str]]:
        return [call for call in self.calls if call != LIST]


def _release_dist(tmp_path: Path) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    _write(dist / WHEEL, "wheel")
    _write(dist / SDIST, "sdist")
    notes = tmp_path / "release-notes.md"
    _write(notes, "- notes\n")
    return dist, notes


def _row(draft: bool, assets: dict[str, str | None], tag: str = TAG) -> dict[str, Any]:
    rows = [
        {"name": name, "digest": None if body is None else f"sha256:{_sha(body)}"}
        for name, body in assets.items()
    ]
    return {"tag_name": tag, "draft": draft, "assets": rows}


def _existing(draft: bool, assets: dict[str, str | None]) -> FakeGh:
    other = _row(False, {"untaped-9.1.0.tar.gz": "old"}, tag="v9.1.0")
    return FakeGh([other, _row(draft, assets)])


def _publish(tmp_path: Path, gh: FakeGh, version: str = "10.0.0") -> None:
    dist, notes = _release_dist(tmp_path)
    release.publish_github_release(version, TAG, REPO, dist, notes, gh=gh)


def _create(tmp_path: Path, prerelease: list[str]) -> list[str]:
    notes = str(tmp_path / "release-notes.md")
    create = ["release", "create", TAG, "--repo", REPO, "--draft", "--verify-tag"]
    return [*create, "--title", TAG, "--notes-file", notes, *prerelease]


def _upload(tmp_path: Path, *names: str, clobber: bool = False) -> list[str]:
    flags = ["--clobber"] if clobber else []
    paths = [str(tmp_path / "dist" / name) for name in names]
    return ["release", "upload", TAG, "--repo", REPO, *flags, *paths]


@pytest.mark.parametrize(
    ("version", "prerelease"), [("10.0.0", []), ("10.0.0rc1", ["--prerelease"])]
)
def test_an_absent_release_is_drafted_uploaded_then_published(
    tmp_path: Path, version: str, prerelease: list[str]
) -> None:
    gh = FakeGh([_row(False, {}, tag="v9.1.0")])
    _publish(tmp_path, gh, version)
    assert gh.calls == [
        LIST,
        _create(tmp_path, prerelease),
        _upload(tmp_path, WHEEL, SDIST),
        PUBLISH,
    ]


def test_a_rerun_after_a_failed_upload_completes_the_same_draft(tmp_path: Path) -> None:
    gh = FakeGh(fail="upload")
    with pytest.raises(release.ReleaseError, match=r"^gh release upload failed: upload failed$"):
        _publish(tmp_path, gh)
    first = list(gh.calls)
    _publish(tmp_path, gh)
    assert gh.calls[len(first) :] == [LIST, _upload(tmp_path, WHEEL, SDIST), PUBLISH]
    assert [row["draft"] for row in gh.releases] == [False]


@pytest.mark.parametrize(
    ("assets", "uploaded", "clobbered"),
    [
        ({WHEEL: "wheel"}, [SDIST], []),
        ({WHEEL: "other", SDIST: "sdist"}, [], [WHEEL]),
        ({WHEEL: None, SDIST: "sdist"}, [], [WHEEL]),
        ({WHEEL: "wheel", SDIST: "sdist"}, [], []),
    ],
    ids=["missing", "different", "no-digest", "matching"],
)
def test_a_draft_uploads_missing_and_clobbers_different_assets_then_publishes(
    tmp_path: Path,
    assets: dict[str, str | None],
    uploaded: list[str],
    clobbered: list[str],
) -> None:
    gh = _existing(True, assets)
    _publish(tmp_path, gh)
    uploads = [_upload(tmp_path, *uploaded)] if uploaded else []
    clobbers = [_upload(tmp_path, *clobbered, clobber=True)] if clobbered else []
    assert gh.writes == [*uploads, *clobbers, PUBLISH]


def test_a_draft_with_an_unexpected_asset_is_refused(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: "wheel", "stray.txt": "x"})
    with pytest.raises(release.ReleaseError) as caught:
        _publish(tmp_path, gh)
    assert str(caught.value) == f"draft release {TAG} has unexpected assets: stray.txt"
    assert gh.writes == []


def test_a_published_release_with_the_same_assets_is_a_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = _existing(False, {WHEEL: "wheel", SDIST: "sdist"})
    _publish(tmp_path, gh)
    assert gh.writes == []
    assert capsys.readouterr().out.strip() == f"release {TAG} already published with these assets"


@pytest.mark.parametrize(
    ("assets", "problems"),
    [
        ({WHEEL: "other", SDIST: "sdist"}, f"different: {WHEEL}"),
        ({WHEEL: "wheel"}, f"missing: {SDIST}"),
        ({WHEEL: "wheel", SDIST: "sdist", "stray.txt": "x"}, "unexpected: stray.txt"),
        (
            {WHEEL: "other", "stray.txt": "x"},
            f"different: {WHEEL}; missing: {SDIST}; unexpected: stray.txt",
        ),
    ],
    ids=["different", "missing", "unexpected", "every-kind"],
)
def test_a_published_release_with_other_assets_is_refused(
    tmp_path: Path, assets: dict[str, str | None], problems: str
) -> None:
    gh = _existing(False, assets)
    with pytest.raises(release.ReleaseError) as caught:
        _publish(tmp_path, gh)
    assert str(caught.value) == f"release {TAG} is already published with other assets: {problems}"
    assert gh.writes == []


def test_two_releases_with_the_tag_are_refused(tmp_path: Path) -> None:
    gh = FakeGh([_row(True, {}), _row(False, {})])
    with pytest.raises(release.ReleaseError, match=r"^2 releases are tagged v10\.0\.0$"):
        _publish(tmp_path, gh)
    assert gh.writes == []


@pytest.mark.parametrize("stderr", ["gh: Not Found (HTTP 404)", "gh: Server Error (HTTP 500)"])
def test_a_failed_release_list_is_refused(tmp_path: Path, stderr: str) -> None:
    gh = FakeGh(list_error=stderr + "\n")
    with pytest.raises(release.ReleaseError) as caught:
        _publish(tmp_path, gh)
    assert str(caught.value) == f"could not list releases: {stderr}"
    assert gh.writes == []


# --- tag --------------------------------------------------------------------

HEAD = "c" * 40
OTHER = "d" * 40
MATCHING = ["api", "--paginate", "--jq", ".[]", f"repos/{REPO}/git/matching-refs/tags/{TAG}"]
TAG_FIELDS = [
    *("-f", f"tag={TAG}", "-f", "message=untaped 10.0.0", "-f", f"object={HEAD}"),
    *("-f", "type=commit", "-f", "tagger[name]=github-actions[bot]"),
    *("-f", "tagger[email]=41898282+github-actions[bot]@users.noreply.github.com"),
]
POST_TAG = ["api", "--method", "POST", *TAG_FIELDS, f"repos/{REPO}/git/tags"]
POST_REF = [
    *("api", "--method", "POST", "-f", f"ref=refs/tags/{TAG}", "-f", "sha=t-new"),
    f"repos/{REPO}/git/refs",
]


class FakeTagGh:
    """A fake ``gh api`` over a repository's tag refs.

    ``refs`` maps a tag name to ``(object type, sha)``; ``objects`` maps an
    annotated tag object's sha to the commit it names. ``race`` is a
    ``(type, sha)`` another run plants just before this one's ref POST,
    which then fails as GitHub's does. ``list_error`` fails the listing, and
    ``fail`` maps the last path segment of a POST (``tags``, ``refs``) to the
    stderr it fails with instead.
    """

    def __init__(
        self,
        refs: dict[str, tuple[str, str]] | None = None,
        *,
        race: tuple[str, str] | None = None,
        list_error: str = "",
        fail: dict[str, str] | None = None,
    ) -> None:
        self.refs = refs or {}
        self.objects = {"t-old": OTHER, "t-head": HEAD}
        self.race = race
        self.list_error = list_error
        self.fail = fail or {}
        self.calls: list[list[str]] = []

    def __call__(self, *argv: str) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        path = args[-1]
        if args == MATCHING:
            if self.list_error:
                return subprocess.CompletedProcess(args, 1, "", self.list_error)
            prefix = path.rsplit("/", 1)[1]
            rows = [
                {"ref": f"refs/tags/{name}", "object": {"type": kind, "sha": sha}}
                for name, (kind, sha) in self.refs.items()
                if name.startswith(prefix)
            ]
            return self._ok(args, "".join(json.dumps(row) + "\n" for row in rows))
        if "POST" in args and path.rsplit("/", 1)[1] in self.fail:
            return subprocess.CompletedProcess(args, 1, "", self.fail[path.rsplit("/", 1)[1]])
        if path.endswith("/git/tags"):
            self.objects["t-new"] = HEAD
            return self._ok(args, json.dumps({"sha": "t-new"}))
        if path.endswith("/git/refs"):
            if self.race is not None:
                self.refs[TAG] = self.race
            if TAG in self.refs:
                body = json.dumps({"message": "Reference already exists"})
                return subprocess.CompletedProcess(args, 1, body, "gh: Reference already exists")
            self.refs[TAG] = ("tag", "t-new")
            return self._ok(args, "{}")
        sha = path.rsplit("/", 1)[1]
        return self._ok(args, json.dumps({"object": {"type": "commit", "sha": self.objects[sha]}}))

    @staticmethod
    def _ok(args: list[str], out: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, out, "")


def _tag(gh: FakeTagGh) -> str:
    return release.create_tag(TAG, REPO, HEAD, gh=gh)


def test_an_absent_tag_is_created_on_the_built_commit() -> None:
    gh = FakeTagGh({"v10.0.0rc1": ("tag", "t-old")})
    assert _tag(gh) == f"tagged {TAG} at {HEAD}"
    assert gh.calls == [MATCHING, POST_TAG, POST_REF]
    assert gh.refs[TAG] == ("tag", "t-new")


@pytest.mark.parametrize(
    ("ref", "reads"),
    [(("tag", "t-head"), [f"repos/{REPO}/git/tags/t-head"]), (("commit", HEAD), [])],
    ids=["annotated", "lightweight"],
)
def test_a_tag_already_on_the_built_commit_is_kept(ref: tuple[str, str], reads: list[str]) -> None:
    gh = FakeTagGh({TAG: ref})
    assert _tag(gh) == f"{TAG} already at {HEAD}"
    assert gh.calls == [MATCHING, *(["api", path] for path in reads)]


@pytest.mark.parametrize("ref", [("tag", "t-old"), ("commit", OTHER)], ids=["annotated", "light"])
def test_a_tag_on_another_commit_is_refused(ref: tuple[str, str]) -> None:
    gh = FakeTagGh({TAG: ref})
    with pytest.raises(release.ReleaseError) as caught:
        _tag(gh)
    assert str(caught.value) == (
        f"{TAG} points at {OTHER}, not the built commit {HEAD}; v* tags cannot move: "
        "release a patch version, or delete the wrong tag with the ruleset relaxed and re-run"
    )
    assert not any("POST" in call for call in gh.calls)


def test_losing_the_race_to_the_same_commit_is_fine() -> None:
    gh = FakeTagGh(race=("tag", "t-head"))
    assert _tag(gh) == f"{TAG} already at {HEAD}"
    peel = ["api", f"repos/{REPO}/git/tags/t-head"]
    assert gh.calls == [MATCHING, POST_TAG, POST_REF, MATCHING, peel]


def test_losing_the_race_to_another_commit_is_refused() -> None:
    gh = FakeTagGh(race=("commit", OTHER))
    with pytest.raises(release.ReleaseError, match=rf"^{TAG} points at {OTHER}, not the built"):
        _tag(gh)
    assert gh.calls == [MATCHING, POST_TAG, POST_REF, MATCHING]


@pytest.mark.parametrize(
    ("fail", "error"),
    [
        ({"tags": "gh: Server Error (HTTP 500)"}, f"gh api repos/{REPO}/git/tags failed: "),
        ({"refs": "gh: Server Error (HTTP 500)"}, f"gh api repos/{REPO}/git/refs failed: "),
        ({"refs": "gh: Reference already exists"}, f"could not create {TAG}: "),
    ],
    ids=["tag-object", "ref", "ref-exists-but-absent"],
)
def test_a_failed_creation_is_refused(fail: dict[str, str], error: str) -> None:
    gh = FakeTagGh(fail=fail)
    with pytest.raises(release.ReleaseError) as caught:
        _tag(gh)
    assert str(caught.value) == error + next(iter(fail.values()))


def test_a_failed_tag_listing_is_refused() -> None:
    gh = FakeTagGh(list_error="gh: Server Error (HTTP 500)\n")
    with pytest.raises(release.ReleaseError) as caught:
        _tag(gh)
    assert str(caught.value) == "could not list tags: gh: Server Error (HTTP 500)"
    assert gh.calls == [MATCHING]


@pytest.mark.parametrize("tag", ["10.0.0", "v10.0", "vX"])
def test_a_tag_that_is_not_v_and_a_version_is_refused(tag: str) -> None:
    gh = FakeTagGh()
    with pytest.raises(release.ReleaseError, match="is not v followed by a release version"):
        release.create_tag(tag, REPO, HEAD, gh=gh)
    assert gh.calls == []


# --- CLI --------------------------------------------------------------------


def _main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of ``release.main(argv)``."""
    code = release.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_the_script_checks_this_repository() -> None:
    """The one subprocess test: it covers the ``__main__`` guard, as CI calls it."""
    version = release.release_version(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "check"], capture_output=True, text=True, check=False
    )
    count = len(release.packages(REPO_ROOT))
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        f"ok: {count} package(s), 0 artifact(s) for {version}\n",
        "",
    )


def test_check_with_dist_fails_on_a_stray_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    _project(root, "untaped", "10.0.0")
    dist = tmp_path / "dist"
    _write(dist / ".gitignore", "")
    _built(dist, "untaped-10.0.0", PINNED)
    argv = ["--root", str(root), "check", "10.0.0", "--dist", str(dist)]
    assert _main(capsys, *argv) == (1, "", "unexpected: .gitignore\n")
    (dist / ".gitignore").unlink()
    assert _main(capsys, *argv) == (0, "ok: 1 package(s), 2 artifact(s) for 10.0.0\n", "")


def test_check_with_dist_fails_on_a_page_that_links_to_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    _project(root, "untaped", "10.0.0")
    dist = tmp_path / "dist"
    _built(dist, "untaped-10.0.0", f"See [docs]({DOCS}/blob/main/docs/a.md).")
    argv = ["--root", str(root), "check", "10.0.0", "--dist", str(dist)]
    assert _main(capsys, *argv) == (
        1,
        "",
        f"untaped-10.0.0-py3-none-any.whl links to main: {DOCS}/blob/main\n"
        f"untaped-10.0.0.tar.gz links to main: {DOCS}/blob/main\n",
    )


def test_the_readmes_command_pins_every_package_readme(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _split_repo(tmp_path)
    readmes = {
        "untaped-github": ('readme = "README.md"', "README.md", f"[a]({DOCS}/blob/main/a.md)"),
        "untaped-awx": ('readme = { file = "docs/PYPI.md" }', "docs/PYPI.md", f"{DOCS}/tree/main"),
        "untaped-jira": ('readme = "README.md"', "README.md", "No links."),
    }
    for package, (field, file, text) in readmes.items():
        pyproject = root / "packages" / package / "pyproject.toml"
        _write(pyproject, pyproject.read_text() + field + "\n")
        _write(root / "packages" / package / file, text + "\n")
    argv = ["--root", str(root), "readmes", "10.1.0"]
    assert _main(capsys, *argv) == (0, "pinned README links to v10.1.0 in 2 file(s)\n", "")
    assert {
        package: (root / "packages" / package / file).read_text()
        for package, (_, file, _) in readmes.items()
    } == {
        "untaped-github": f"[a]({DOCS}/blob/v10.1.0/a.md)\n",
        "untaped-awx": f"{DOCS}/tree/v10.1.0\n",
        "untaped-jira": "No links.\n",
    }
    assert _main(capsys, *argv) == (0, "pinned README links to v10.1.0 in 0 file(s)\n", "")


def test_the_readmes_command_refuses_a_malformed_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert _main(capsys, "--root", str(tmp_path), "readmes", "v10.0.0") == (
        1,
        "",
        "version v10.0.0 is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN\n",
    )


def test_the_version_command_prints_the_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert _main(capsys, "--root", str(tmp_path), "version") == (0, "10.0.0\n", "")


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], (0, "index ok: 1 present, 1 to upload\n", "", 1)),
        (["--complete"], (1, "", f"missing on testpypi: {SDIST}\n", 2)),
    ],
    ids=["before-publish", "complete"],
)
def test_the_index_command_waits_for_every_file_only_with_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    expected: tuple[int, str, str, int],
) -> None:
    root, dist = _dist(tmp_path)
    calls: list[str] = []

    def fetch_json(url: str) -> dict[str, Any] | None:
        calls.append(url)
        return _fetch({WHEEL: "wheel"})(url)

    monkeypatch.setattr(release, "fetch_json", fetch_json)
    monkeypatch.setattr(release, "INDEX_TRIES", 2)
    monkeypatch.setattr(release, "INDEX_DELAY", 0)
    argv = ["--root", str(root), "index", "10.0.0", "--dist", str(dist), "--index", "testpypi"]
    code = release.main([*argv, *flags])
    out, err = capsys.readouterr()
    assert (code, out, err, len(calls)) == expected


def _git(cwd: Path, *args: str) -> str:
    config = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    result = subprocess.run(
        ["git", *config, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, version: str | None = None, message: str = "change") -> str:
    """Commit in ``repo`` (at ``version`` when given, else an empty commit); its SHA."""
    if version is not None:
        _project(repo, "untaped", version)
        _git(repo, "add", ".")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _clone_of_main(tmp_path: Path) -> Path:
    """A clone of an origin whose ``main`` holds an untaped 10.0.0 project."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "--initial-branch=main")
    _commit(origin, "10.0.0", "release")
    _git(tmp_path, "clone", "-q", str(origin), "work")
    return tmp_path / "work"


def _route(
    capsys: pytest.CaptureFixture[str],
    root: Path,
    *,
    event: str = "push",
    ref_type: str = "branch",
    ref_name: str = "main",
    repository: str = release.PUBLISHING_REPOSITORY,
    before: str | None = None,
) -> tuple[int, str, str]:
    argv = ["--root", str(root), "route", "--event", event, "--ref-type", ref_type]
    argv += ["--ref-name", ref_name, "--repository", repository]
    return _main(capsys, *argv, *(["--before", before] if before is not None else []))


def _routed(version: str, index: str, tag: str, reason: str) -> tuple[int, str, str]:
    return 0, f"version={version}\nindex={index}\ntag={tag}\nreason={reason}\n", ""


class _Bumped(NamedTuple):
    """A clone of ``main`` whose last commit (HEAD) moved untaped 10.0.0 to 10.1.0."""

    origin: Path
    work: Path
    before: str
    head: str


def _bumped(tmp_path: Path) -> _Bumped:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "--initial-branch=main")
    before = _commit(origin, "10.0.0", "10.0.0")
    head = _commit(origin, "10.1.0", "release 10.1.0")
    _git(tmp_path, "clone", "-q", str(origin), "work")
    return _Bumped(origin, tmp_path / "work", before, head)


def test_a_fork_publishes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    reason = "someone/untaped is not the publishing repository: nothing to publish"
    assert _route(capsys, tmp_path, repository="someone/untaped") == _routed(
        "10.0.0", "none", "", reason
    )


@pytest.mark.parametrize(("ref_type", "ref_name"), [("branch", "claude/x"), ("tag", "v10.0.0")])
def test_a_dispatch_on_any_ref_is_a_rehearsal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], ref_type: str, ref_name: str
) -> None:
    _project(tmp_path, "untaped", "10.0.0rc1")
    routed = _route(
        capsys, tmp_path, event="workflow_dispatch", ref_type=ref_type, ref_name=ref_name, before=""
    )
    assert routed == _routed("10.0.0rc1", "testpypi", "", "rehearsal: TestPyPI only")


@pytest.mark.parametrize("repository", [release.PUBLISHING_REPOSITORY, "someone/untaped"])
def test_a_malformed_version_is_refused_on_every_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], repository: str
) -> None:
    _project(tmp_path, "untaped", "10.0")
    assert _route(capsys, tmp_path, event="workflow_dispatch", repository=repository) == (
        1,
        "",
        "version 10.0 is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN\n",
    )


def test_a_pushed_tag_on_main_is_a_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = _clone_of_main(tmp_path)
    assert _route(capsys, work, ref_type="tag", ref_name="v10.0.0", before="") == _routed(
        "10.0.0", "pypi", "v10.0.0", "v10.0.0 is on main: releasing 10.0.0"
    )


@pytest.mark.parametrize("tag", ["v10.0.1", "10.0.0", "v10.0.0rc1", "v10.0.0\n"])
def test_a_pushed_tag_that_is_not_v_and_the_version_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], tag: str
) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert _route(capsys, tmp_path, ref_type="tag", ref_name=tag) == (
        1,
        "",
        f"tag {tag} does not match the package version 10.0.0\n",
    )


def test_a_pushed_tag_off_main_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = _clone_of_main(tmp_path)
    head = _commit(work, message="not on main")
    assert _route(capsys, work, ref_type="tag", ref_name="v10.0.0") == (
        1,
        "",
        f"commit {head} is not on main\n",
    )


def test_a_pushed_tag_outside_a_git_checkout_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert _route(capsys, tmp_path, ref_type="tag", ref_name="v10.0.0") == (
        1,
        "",
        f"{tmp_path.resolve()} is not a git checkout\n",
    )


def test_a_push_to_main_that_changes_the_version_releases_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bumped = _bumped(tmp_path)
    reason = f"10.1.0 replaces 10.0.0 at {bumped.before[:7]} and has no tag yet: releasing it"
    assert _route(capsys, bumped.work, before=bumped.before) == _routed(
        "10.1.0", "pypi", "v10.1.0", reason
    )


@pytest.mark.parametrize("annotated", [True, False], ids=["annotated", "lightweight"])
def test_a_push_to_main_whose_tag_is_already_at_head_releases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], annotated: bool
) -> None:
    """A re-run after the tag step, or a hand tag on this commit: every step is idempotent."""
    bumped = _bumped(tmp_path)
    _git(bumped.origin, "tag", *(["-a", "-m", "untaped 10.1.0"] if annotated else []), "v10.1.0")
    # The before commit is not even read: the tag at HEAD decides.
    reason = "v10.1.0 is already at this commit (a re-run or a hand tag): releasing 10.1.0"
    assert _route(capsys, bumped.work, before="0" * 39 + "1") == _routed(
        "10.1.0", "pypi", "v10.1.0", reason
    )


@pytest.mark.parametrize("annotated", [True, False], ids=["annotated", "lightweight"])
def test_a_push_to_main_whose_tag_is_elsewhere_publishes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], annotated: bool
) -> None:
    bumped = _bumped(tmp_path)
    tag = [*(["-a", "-m", "untaped 10.1.0"] if annotated else []), "v10.1.0", bumped.before]
    _git(bumped.origin, "tag", *tag)
    reason = f"v10.1.0 already points at {bumped.before[:7]}, not this commit: nothing to publish"
    assert _route(capsys, bumped.work, before=bumped.before) == _routed(
        "10.1.0", "none", "", reason
    )


@pytest.mark.parametrize(
    ("tagged", "reason"),
    [
        (False, "10.1.0 is unchanged by this push and not released yet: nothing to publish"),
        (True, "10.1.0 is unchanged by this push: nothing to publish"),
    ],
    ids=["untagged", "tagged"],
)
def test_a_push_to_main_that_keeps_the_version_publishes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], tagged: bool, reason: str
) -> None:
    """A dependency bump after a release, or after a rejected one: no approval mail."""
    bumped = _bumped(tmp_path)
    if tagged:
        _git(bumped.origin, "tag", "v10.1.0")
    _commit(bumped.origin, "10.1.0", "bump a dependency")
    _git(bumped.work, "pull", "-q")
    assert _route(capsys, bumped.work, before=bumped.head) == _routed("10.1.0", "none", "", reason)


def test_a_first_push_counts_as_a_changed_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bumped = _bumped(tmp_path)
    reason = "10.1.0 is new and has no tag yet: releasing it"
    assert _route(capsys, bumped.work, before="0" * 40) == _routed(
        "10.1.0", "pypi", "v10.1.0", reason
    )


def test_a_malformed_pyproject_at_before_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "--initial-branch=main")
    _write(origin / "pyproject.toml", "[project\n")
    _git(origin, "add", ".")
    before = _commit(origin, message="broken")
    _commit(origin, "10.1.0", "fixed")
    _git(tmp_path, "clone", "-q", str(origin), "work")
    code, out, err = _route(capsys, tmp_path / "work", before=before)
    assert (code, out) == (1, "")
    assert err.startswith(f"pyproject.toml at {before} is not valid TOML: ")


def test_a_package_absent_at_before_counts_as_a_changed_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "--initial-branch=main")
    before = _commit(origin, message="empty")
    _commit(origin, "10.1.0", "add the package")
    _git(tmp_path, "clone", "-q", str(origin), "work")
    assert _route(capsys, tmp_path / "work", before=before) == _routed(
        "10.1.0", "pypi", "v10.1.0", "10.1.0 is new and has no tag yet: releasing it"
    )


@pytest.mark.parametrize(
    ("before", "error"),
    [
        (None, "a push to main needs --before"),
        ("1" * 40, f"commit {'1' * 40} is not in this checkout"),
    ],
    ids=["missing", "unreadable"],
)
def test_a_push_to_main_needs_a_readable_before_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], before: str | None, error: str
) -> None:
    bumped = _bumped(tmp_path)
    assert _route(capsys, bumped.work, before=before) == (1, "", f"{error}\n")


def test_a_failed_tag_lookup_fails_the_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bumped = _bumped(tmp_path)
    _git(bumped.work, "remote", "set-url", "origin", str(tmp_path / "gone"))
    code, out, err = _route(capsys, bumped.work, before=bumped.before)
    assert (code, out) == (1, "")
    assert err.startswith("git ls-remote failed")


@pytest.mark.parametrize(
    ("event", "ref_type", "ref_name", "error"),
    [
        ("push", "branch", "claude/x", "nothing releases from a push of branch claude/x"),
        ("pull_request", "branch", "main", "nothing releases from a pull_request of branch main"),
    ],
)
def test_any_other_event_or_branch_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    event: str,
    ref_type: str,
    ref_name: str,
    error: str,
) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert _route(capsys, tmp_path, event=event, ref_type=ref_type, ref_name=ref_name) == (
        1,
        "",
        f"{error}\n",
    )


@pytest.mark.parametrize("tagged", [False, True], ids=["absent", "present"])
def test_the_tag_command_tags_the_checked_out_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tagged: bool,
) -> None:
    work = _clone_of_main(tmp_path)
    head = _git(work, "rev-parse", "HEAD")
    gh = FakeTagGh({TAG: ("commit", head)} if tagged else {})
    monkeypatch.setattr(release, "_run", lambda exe, *args: gh(*args))
    printed = f"{TAG} already at {head}" if tagged else f"tagged {TAG} at {head}"
    assert _main(capsys, "--root", str(work), "tag", TAG, "--repo", REPO) == (
        0,
        printed + "\n",
        "",
    )
    if not tagged:
        expected = [arg.replace(HEAD, head) for arg in POST_TAG]
        assert gh.calls[1] == expected


def test_the_tag_command_needs_a_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(capsys, "--root", str(tmp_path), "tag", TAG, "--repo", REPO) == (
        1,
        "",
        f"{tmp_path.resolve()} is not a git checkout\n",
    )


def test_check_defaults_to_the_package_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, "untaped", "10.0.0rc1")
    assert _main(capsys, "--root", str(tmp_path), "check") == (
        0,
        "ok: 1 package(s), 0 artifact(s) for 10.0.0rc1\n",
        "",
    )


def test_the_notes_command_prints_the_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "CHANGELOG.md", CHANGELOG)
    assert _main(capsys, "--root", str(tmp_path), "notes", "10.0.0") == (
        0,
        "- Core\n  - **New:** x\n",
        "",
    )


def test_this_repository_satisfies_the_release_metadata() -> None:
    """The package declares what PyPI shows."""
    project = release.packages(REPO_ROOT)["untaped"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project.get("readme") == "README.md"
    assert not any(str(item).startswith("License ::") for item in project.get("classifiers", []))
