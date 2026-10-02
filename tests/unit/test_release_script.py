"""scripts/release.py: package discovery, version/pin, artifact, index, notes, smoke and release."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
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
    expected = release.expected_artifacts(root, "10.0.0")
    assert len(expected) == 2 * len(release.packages(root))
    assert {
        "untaped-10.0.0-py3-none-any.whl",
        "untaped-10.0.0.tar.gz",
        "untaped_awx-10.0.0-py3-none-any.whl",
        "untaped_awx-10.0.0.tar.gz",
    } <= expected


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
READ = ["api", f"repos/{REPO}/releases/tags/{TAG}"]
PUBLISH = ["release", "edit", TAG, "--repo", REPO, "--draft=false"]


class FakeGh:
    """Records ``gh`` calls; the release read returns a scripted result, writes succeed."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.read = subprocess.CompletedProcess(READ, returncode, stdout, stderr)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args == READ:
            return self.read
        return subprocess.CompletedProcess(args, 0, "", "")

    @property
    def writes(self) -> list[list[str]]:
        return [call for call in self.calls if call != READ]


def _release_dist(tmp_path: Path) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    _write(dist / WHEEL, "wheel")
    _write(dist / SDIST, "sdist")
    notes = tmp_path / "release-notes.md"
    _write(notes, "- notes\n")
    return dist, notes


def _existing(draft: bool, assets: dict[str, str]) -> FakeGh:
    rows = [{"name": name, "digest": f"sha256:{_sha(body)}"} for name, body in assets.items()]
    return FakeGh(stdout=json.dumps({"draft": draft, "assets": rows}))


def _publish(tmp_path: Path, gh: FakeGh, version: str = "10.0.0") -> None:
    dist, notes = _release_dist(tmp_path)
    release.publish_github_release(version, TAG, REPO, dist, notes, gh=gh)


@pytest.mark.parametrize(
    ("version", "prerelease"), [("10.0.0", []), ("10.0.0rc1", ["--prerelease"])]
)
def test_an_absent_release_is_drafted_uploaded_then_published(
    tmp_path: Path, version: str, prerelease: list[str]
) -> None:
    gh = FakeGh(returncode=1, stderr="gh: Not Found (HTTP 404)\n")
    _publish(tmp_path, gh, version)
    dist = tmp_path / "dist"
    notes = tmp_path / "release-notes.md"
    assert gh.calls == [
        READ,
        [
            "release",
            "create",
            TAG,
            "--repo",
            REPO,
            "--draft",
            "--verify-tag",
            "--title",
            TAG,
            "--notes-file",
            str(notes),
            *prerelease,
        ],
        ["release", "upload", TAG, "--repo", REPO, str(dist / WHEEL), str(dist / SDIST)],
        PUBLISH,
    ]


def test_a_draft_missing_an_asset_uploads_only_that_asset(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: "wheel"})
    _publish(tmp_path, gh)
    assert gh.writes == [
        ["release", "upload", TAG, "--repo", REPO, str(tmp_path / "dist" / SDIST)],
        PUBLISH,
    ]


def test_a_draft_with_a_different_asset_clobbers_it(tmp_path: Path) -> None:
    gh = _existing(True, {WHEEL: "other", SDIST: "sdist"})
    _publish(tmp_path, gh)
    assert gh.writes == [
        ["release", "upload", TAG, "--repo", REPO, "--clobber", str(tmp_path / "dist" / WHEEL)],
        PUBLISH,
    ]


def test_a_published_release_with_the_same_assets_is_a_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = _existing(False, {WHEEL: "wheel", SDIST: "sdist"})
    _publish(tmp_path, gh)
    assert gh.writes == []
    assert capsys.readouterr().out.strip() == f"release {TAG} already published with these assets"


def test_a_published_release_with_other_assets_is_refused(tmp_path: Path) -> None:
    gh = _existing(False, {WHEEL: "other", "stray.txt": "x"})
    with pytest.raises(release.ReleaseError) as caught:
        _publish(tmp_path, gh)
    assert str(caught.value) == (
        f"release {TAG} is already published with other assets: "
        f"different: {WHEEL}; missing: {SDIST}; unexpected: stray.txt"
    )
    assert gh.writes == []


def test_a_failed_read_that_is_not_a_404_is_refused(tmp_path: Path) -> None:
    gh = FakeGh(returncode=1, stderr="gh: Server Error (HTTP 500)\n")
    with pytest.raises(
        release.ReleaseError, match=r"^could not read release v10\.0\.0: .*HTTP 500"
    ):
        _publish(tmp_path, gh)
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
    assert result.stdout.strip() == "ok: 1 package(s), 2 artifact(s) for 10.0.0"


def test_the_notes_command_prints_the_section(tmp_path: Path) -> None:
    _write(tmp_path / "CHANGELOG.md", CHANGELOG)
    result = _cli("--root", str(tmp_path), "notes", "10.0.0")
    assert (result.returncode, result.stdout) == (0, "- Core\n  - **New:** x\n")


def test_this_repository_satisfies_the_release_metadata() -> None:
    """Kept from the deleted test_release_workflow.py: the package declares what PyPI shows."""
    project = release.packages(SCRIPT.parents[1])["untaped"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project.get("readme") == "README.md"
    assert not any(str(item).startswith("License ::") for item in project.get("classifiers", []))
