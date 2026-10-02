"""scripts/release.py: package discovery, version/pin, artifact, index, notes, smoke and release."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from untaped.bootstrap import BUILTIN_CAPABILITIES

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load()


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


CAPS = ("awx", "ansible", "github", "jira", "workspace", "recipe")


def _split_repo(root: Path, version: str = "10.0.0") -> Path:
    """The §1 layout: a virtual root, core and six capability packages, one excluded example."""
    _write(
        root / "pyproject.toml",
        "[tool.uv.workspace]\n"
        'members = ["packages/*", "examples/*"]\n'
        'exclude = ["examples/untaped-hello"]\n',
    )
    pins = {cap: [f"untaped-{cap}=={version}"] for cap in CAPS}
    _project(
        root / "packages" / "untaped",
        "untaped",
        version,
        ("pydantic>=2",),
        {**pins, "all": [f"untaped-{c}=={version}" for c in CAPS]},
    )
    for cap in CAPS:
        deps = (f"untaped=={version}",) + (
            (f"untaped-github=={version}",) if cap in ("ansible", "workspace") else ()
        )
        _project(root / "packages" / f"untaped-{cap}", f"untaped-{cap}", version, deps)
    _project(root / "examples" / "untaped-hello", "untaped-hello", "0.1.0", ("untaped>=10",))
    return root


# --- discovery and versions -------------------------------------------------


def test_a_single_project_repo_is_one_package(tmp_path: Path) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert list(release.packages(tmp_path)) == ["untaped"]
    assert release.release_version(tmp_path) == "10.0.0"


def test_the_split_layout_finds_seven_packages_and_skips_the_example(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    assert sorted(release.packages(root)) == sorted(["untaped", *(f"untaped-{c}" for c in CAPS)])
    assert release.release_version(root) == "10.0.0"
    assert release.version_errors(root, "10.0.0") == []


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
    with pytest.raises(release.ReleaseError, match="no package named untaped"):
        release.release_version(tmp_path)


@pytest.mark.parametrize(
    "version", ["10.0", "v10.0.0", "10.0.0.dev1", "10.0.0+local", "10.0.0-rc1"]
)
def test_a_malformed_release_version_is_refused(tmp_path: Path, version: str) -> None:
    _project(tmp_path, "untaped", version)
    with pytest.raises(release.ReleaseError, match=re.escape(f"version {version} is not X.Y.Z")):
        release.release_version(tmp_path)


@pytest.mark.parametrize("version", ["10.0.0", "10.0.0a1", "10.0.0b2", "10.0.0rc3"])
def test_release_versions_accept_finals_and_prereleases(tmp_path: Path, version: str) -> None:
    _project(tmp_path, "untaped", version)
    assert release.release_version(tmp_path) == version


def test_version_errors_report_a_malformed_tag_first(tmp_path: Path) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert release.version_errors(tmp_path, "v10.0.0") == [
        "version v10.0.0 is not X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN",
        "untaped is at 10.0.0, not v10.0.0",
    ]


def test_a_trailing_newline_is_not_a_release_version(tmp_path: Path) -> None:
    _project(tmp_path, "untaped", "10.0.0")
    assert release.version_errors(tmp_path, "10.0.0\n")[0].startswith("version 10.0.0\n is not")


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
    assert release.version_errors(tmp_path, "10.0.1") == [
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
    assert release.version_errors(root, "10.0.0") == [
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
    assert release.version_errors(root, "10.0.0") == []


# --- artifacts --------------------------------------------------------------


def test_expected_artifacts_normalize_names(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    stems = ["untaped", *(f"untaped_{cap}" for cap in CAPS)]
    assert release.expected_artifacts(root, "10.0.0") == {
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
    assert release.artifact_errors(root, "10.0.0", dist) == [
        "missing: untaped-10.0.0.tar.gz",
        "unexpected: .gitignore",
        "unexpected: untaped-9.1.0.tar.gz",
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
    remote: dict[str, str] | None,
    complete: bool,
    expected: tuple[list[str], int, int],
) -> None:
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
    result = release.wait_for_index(
        root, "10.0.0", dist, index="testpypi", fetch=fetch, sleep=delays.append
    )
    assert result == ([], 2, 0)
    assert delays == [10, 10]


def test_a_conflict_fails_the_wait_without_sleeping(tmp_path: Path) -> None:
    root, dist = _dist(tmp_path)
    delays: list[float] = []
    fetch = _sequence(_payload({WHEEL: "other"}))
    errors, _, _ = release.wait_for_index(
        root, "10.0.0", dist, index="testpypi", fetch=fetch, sleep=delays.append
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

    errors, present, to_upload = release.wait_for_index(
        root, "10.0.0", dist, index="testpypi", fetch=fetch, sleep=delays.append
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
        release.wait_for_index(
            root, "10.0.0", dist, index="testpypi", fetch=fetch, sleep=delays.append
        )
    assert delays == [10]


def test_each_package_is_checked_against_its_own_index_entry(tmp_path: Path) -> None:
    root = _split_repo(tmp_path)
    dist = tmp_path / "dist"
    for file in release.expected_artifacts(root, "10.0.0"):
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
    names = sorted(["untaped", *(f"untaped-{cap}" for cap in CAPS)])
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


# --- notes ------------------------------------------------------------------

CHANGELOG = (
    "# Changelog\n\n## Unreleased\n\n- next\n\n## 10.0.0\n\n- Core\n  - **New:** x\n\n"
    "## 9.1.0\n\n- old\n"
)


def test_release_notes_are_the_versions_section(tmp_path: Path) -> None:
    _write(tmp_path / "CHANGELOG.md", CHANGELOG)
    assert release.release_notes(tmp_path / "CHANGELOG.md", "10.0.0") == "- Core\n  - **New:** x"


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
        "capability github is missing",
        "capability jira is quarantined",
    ]
    assert release.smoke_errors("10.0.0\n", json.dumps(ROWS[:1]), "10.0.0", ["awx"]) == []


def test_smoke_errors_refuse_output_that_is_not_a_list_of_rows() -> None:
    assert release.smoke_errors("10.0.0", "not json", "10.0.0", ["awx"]) == [
        "untaped capabilities --format json did not print a list of rows"
    ]


BUILTINS = [spec.name for spec in BUILTIN_CAPABILITIES]
FAKE_UNTAPED = """#!/bin/sh
case "$1" in
  --version) echo "$FAKE_VERSION" ;;
  capabilities) echo "$FAKE_ROWS" ;;
  "$FAKE_FAILING") exit 2 ;;
esac
exit 0
"""


def _fake_untaped(tmp_path: Path) -> Path:
    exe = tmp_path / "untaped"
    _write(exe, FAKE_UNTAPED)
    exe.chmod(0o755)
    return exe


def _smoke(
    tmp_path: Path, rows: list[dict[str, str]], failing: str = ""
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "FAKE_VERSION": "10.0.0",
        "FAKE_ROWS": json.dumps(rows),
        "FAKE_FAILING": failing,
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), "smoke", str(_fake_untaped(tmp_path)), "10.0.0"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_smoke_command_passes_a_healthy_install(tmp_path: Path) -> None:
    result = _smoke(tmp_path, [{"name": cap, "status": "ready"} for cap in BUILTINS])
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.strip() == f"smoke ok: untaped 10.0.0, {len(BUILTINS)} capabilities"


def test_the_smoke_command_reports_a_failing_help(tmp_path: Path) -> None:
    failing = BUILTINS[-1]
    result = _smoke(tmp_path, [{"name": cap, "status": "ready"} for cap in BUILTINS], failing)
    assert (result.returncode, result.stderr.strip()) == (1, f"untaped {failing} --help exited 2")


def test_the_smoke_command_reports_a_capability_that_is_not_ready(tmp_path: Path) -> None:
    rows = [{"name": cap, "status": "ready"} for cap in BUILTINS]
    rows[0]["status"] = "quarantined"
    result = _smoke(tmp_path, rows)
    assert (result.returncode, result.stderr.strip()) == (
        1,
        f"capability {BUILTINS[0]} is quarantined",
    )


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

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
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


def test_a_draft_missing_an_asset_uploads_only_that_asset(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: "wheel"})
    _publish(tmp_path, gh)
    assert gh.writes == [_upload(tmp_path, SDIST), PUBLISH]


def test_a_draft_with_a_different_asset_clobbers_it(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: "other", SDIST: "sdist"})
    _publish(tmp_path, gh)
    assert gh.writes == [_upload(tmp_path, WHEEL, clobber=True), PUBLISH]


def test_a_draft_asset_without_a_digest_is_clobbered(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: None, SDIST: "sdist"})
    _publish(tmp_path, gh)
    assert gh.writes == [_upload(tmp_path, WHEEL, clobber=True), PUBLISH]


def test_a_draft_that_already_matches_is_only_published(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: "wheel", SDIST: "sdist"})
    _publish(tmp_path, gh)
    assert gh.writes == [PUBLISH]


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
    ("assets", "problem"),
    [
        ({WHEEL: "other", SDIST: "sdist"}, f"different: {WHEEL}"),
        ({WHEEL: "wheel"}, f"missing: {SDIST}"),
        ({WHEEL: "wheel", SDIST: "sdist", "stray.txt": "x"}, "unexpected: stray.txt"),
    ],
    ids=["different", "missing", "unexpected"],
)
def test_a_published_release_with_other_assets_is_refused(
    tmp_path: Path, assets: dict[str, str | None], problem: str
) -> None:
    gh = _existing(False, assets)
    with pytest.raises(release.ReleaseError) as caught:
        _publish(tmp_path, gh)
    assert str(caught.value) == f"release {TAG} is already published with other assets: {problem}"
    assert gh.writes == []


def test_a_published_release_lists_every_kind_of_problem(tmp_path: Path) -> None:
    gh = _existing(False, {WHEEL: "other", "stray.txt": "x"})
    with pytest.raises(release.ReleaseError) as caught:
        _publish(tmp_path, gh)
    assert str(caught.value) == (
        f"release {TAG} is already published with other assets: "
        f"different: {WHEEL}; missing: {SDIST}; unexpected: stray.txt"
    )


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


# --- CLI against this repository --------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_the_cli_checks_this_repository() -> None:
    root = SCRIPT.parents[1]
    version = _cli("version").stdout.strip()
    assert version == release.release_version(root)
    count = len(release.packages(root))
    good = _cli("check", version)
    assert (good.returncode, good.stdout.strip()) == (
        0,
        f"ok: {count} package(s), 0 artifact(s) for {version}",
    )
    bad = _cli("check", "0.0.0")
    assert bad.returncode == 1
    assert bad.stderr.splitlines()[0] == f"untaped is at {version}, not 0.0.0"


def test_check_with_dist_fails_on_a_stray_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root, "untaped", "10.0.0")
    dist = tmp_path / "dist"
    for name in ("untaped-10.0.0-py3-none-any.whl", "untaped-10.0.0.tar.gz", ".gitignore"):
        _write(dist / name, "")
    result = _cli("--root", str(root), "check", "10.0.0", "--dist", str(dist))
    assert (result.returncode, result.stderr.strip()) == (1, "unexpected: .gitignore")
    (dist / ".gitignore").unlink()
    result = _cli("--root", str(root), "check", "10.0.0", "--dist", str(dist))
    assert (result.returncode, result.stdout.strip()) == (
        0,
        "ok: 1 package(s), 2 artifact(s) for 10.0.0",
    )


def test_the_notes_command_prints_the_section(tmp_path: Path) -> None:
    _write(tmp_path / "CHANGELOG.md", CHANGELOG)
    result = _cli("--root", str(tmp_path), "notes", "10.0.0")
    assert (result.returncode, result.stdout) == (0, "- Core\n  - **New:** x\n")


def test_this_repository_satisfies_the_release_metadata() -> None:
    """The package declares what PyPI shows."""
    project = release.packages(SCRIPT.parents[1])["untaped"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project.get("readme") == "README.md"
    assert not any(str(item).startswith("License ::") for item in project.get("classifiers", []))
