"""A postponement reaches users: the build's generated Deprecated facts, and the release PR's check.

``scripts/changelog.py build`` compares ``tests/repo/surface.txt`` with the
newest release tag's (release candidates included) and writes one line per
removal version that rose, unless an entry of the release names the thing.
On a release PR (``CHANGELOG.md`` has the workspace version's section and
``changelog.d/`` is empty) the section must carry each fact or such an entry,
so a generated line is never dropped or hand-edited away; once the version is
tagged, main's later PRs are no release PR.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
import release  # scripts/release.py

import changelog  # scripts/changelog.py
from repo.support import REPO_ROOT, no_baseline, reachable_tags
from untaped.core.surface import DeprecatedMark, Line, render


def is_release_pr(version: str, text: str, fragments: Sequence[str]) -> bool:
    """Whether the changelog ``text`` has ``version``'s section and no fragment is left."""
    built = re.search(rf"^## {re.escape(version)}$", text, re.MULTILINE) is not None
    return built and not fragments


def section(text: str, version: str) -> str:
    """The body of ``## version`` in ``text``."""
    match = re.search(
        rf"^## {re.escape(version)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    return match[1] if match else ""


def deprecated_entries(body: str) -> list[str]:
    """The entries under a section body's ``### Deprecated``."""
    match = re.search(r"^### Deprecated\n(.*?)(?=^### |\Z)", body, re.MULTILINE | re.DOTALL)
    if match is None:
        return []
    return [entry for entry in re.split(r"(?m)^(?=- )", match[1].strip()) if entry]


def test_the_release_section_carries_every_fact() -> None:
    version = release.release_version(REPO_ROOT)
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not is_release_pr(version, text, changelog.fragment_paths(REPO_ROOT)):
        pytest.skip(f"not a release PR: CHANGELOG.md has no built {version} section to check")
    if f"v{version}" in reachable_tags():
        pytest.skip(f"v{version} is released: its section was checked on its release PR")
    try:
        facts = changelog.surface_facts(REPO_ROOT)
    except changelog.ChangelogError:
        no_baseline()
    # The build compared with the tag before this release; on the release PR that is the newest.
    missing = changelog.missing_facts(deprecated_entries(section(text, version)), facts)

    assert not missing, f"CHANGELOG.md's {version} section lost these generated lines:\n" + (
        "\n".join(f"  {fact.text}" for fact in missing)
    )


@pytest.mark.parametrize(
    ("version", "text", "fragments", "expected"),
    [
        ("11.0.0", "# Changelog\n\n## 11.0.0\n", [], True),
        ("11.0.0rc1", "# Changelog\n\n## 11.0.0rc1\n", [], True),
        ("11.1.0", "# Changelog\n\n## 11.1.0\n", [], True),
        ("11.0.1", "# Changelog\n\n## 11.0.1\n", [], True),
        # A pre-release section is retitled by the final build, so the final version needs its own.
        ("11.0.0", "# Changelog\n\n## 11.0.0rc1\n", [], False),
        # Fragments left in changelog.d/ mean the release has not been built (main before it).
        ("11.0.0", "# Changelog\n\n## 10.0.0\n", ["changelog.d/x.added.md"], False),
        ("11.0.0", "# Changelog\n\n## 10.0.0\n", [], False),
    ],
)
def test_is_release_pr(version: str, text: str, fragments: list[str], expected: bool) -> None:
    assert is_release_pr(version, text, fragments) is expected


# --- the facts --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identity", "name"),
    [
        ("sdk untaped.sdk.report_error(exc)", "report_error"),
        ("sdk untaped.sdk.report_error(item=) default=None", "report_error"),
        ("sdk untaped.sdk.UiContext.message(kind, text)", "UiContext.message"),
        ("sdk untaped.sdk.testing.git.make_repo(path)", "make_repo"),
        ("sdk untaped_acme.api.old()", "old"),
        ("cli untaped awx put", "awx put"),
        ("cli untaped awx test run --dry-run switch", "awx test run --dry-run"),
        ("cli untaped jira me -> untaped jira whoami", "jira me"),
        ("cli untaped jira logs -F -> --follow", "jira logs -F"),
        ("cli untaped plugin check [NAME]@1 default=unset", "plugin check"),
        ("cfg ansible.stale_after_seconds -> ansible.caches.index.max_age_seconds", None),
        ("cfg acme.old_mode boolean default=false", "acme.old_mode"),
        ("behaviour untaped.plaintext-token", "plaintext-token"),
    ],
)
def test_the_short_name_per_area(identity: str, name: str | None) -> None:
    expected = "ansible.stale_after_seconds" if name is None else name
    assert changelog.short_name(identity) == expected


@pytest.mark.parametrize(
    ("entry", "name", "named"),
    [
        ("- `report_error` stays one more major.", "report_error", True),
        ("- `untaped.sdk.report_error` stays.", "report_error", True),
        ("- `report_errors` stays.", "report_error", False),
        ("- `untaped auth migrate --dry-run` stays.", "awx test run --dry-run", False),
        ("- `untaped awx test run\n  --dry-run` stays.", "awx test run --dry-run", True),
        ("- `jira me` and `jira mine` stay.", "jira me", True),
        ("- `jira mine` stays.", "jira me", False),
    ],
)
def test_an_entry_names_a_thing_as_a_whole_token(entry: str, name: str, named: bool) -> None:
    assert changelog.names(entry, name) is named


def _file(*lines: Line) -> str:
    return render(lines)


def test_a_raised_major_is_a_fact() -> None:
    baseline = _file(
        Line("sdk untaped.sdk.report_error(exc)", DeprecatedMark(12)),
        Line("cli untaped awx put", DeprecatedMark()),
        Line("cli untaped jira me -> untaped jira whoami", DeprecatedMark(12)),
    )
    current = _file(
        Line("sdk untaped.sdk.report_error(exc)", DeprecatedMark(13, "Diagnostics.failure")),
        Line("cli untaped awx put", DeprecatedMark(13)),  # undated at the baseline: nothing moved
        Line("cli untaped jira me -> untaped jira whoami", DeprecatedMark(12)),
        Line("cli untaped jira logs -F -> --follow", DeprecatedMark(14)),
    )

    assert changelog.postponement_facts(baseline, current) == [
        changelog.Fact(
            "report_error", "`report_error` will now be removed in 13.0 instead of 12.0."
        )
    ]


def test_a_thing_s_lines_share_one_fact() -> None:
    def lines(major: int) -> list[Line]:
        mark = DeprecatedMark(major, "untaped lint")
        return [
            Line("cli untaped plugin check", mark),
            Line("cli untaped plugin check --format VALUE default=table", mark),
            Line("cli untaped plugin check --format choice=json", mark),
            Line("cli untaped plugin check [NAME]@1 default=unset", mark),
            Line("sdk untaped.sdk.report_error(exc)", DeprecatedMark(major)),
            Line("sdk untaped.sdk.report_error(item=) default=None", DeprecatedMark(major)),
            Line("sdk untaped.sdk.AppContext implement=", DeprecatedMark(major)),
            Line("sdk untaped.sdk.AppContext.diag", DeprecatedMark(major)),
            # Raised further on its own: its own fact.
            Line("cli untaped plugin check --columns VALUE", DeprecatedMark(major + 1)),
        ]

    facts = changelog.postponement_facts(_file(*lines(12)), _file(*lines(13)))

    assert [fact.name for fact in facts] == [
        "plugin check",
        "plugin check --columns",
        "AppContext",
        "report_error",
    ]
    assert facts[1].text.endswith("removed in 14.0 instead of 13.0.")


def test_a_fallen_date_never_reaches_the_build() -> None:
    # test_major_release.py refuses it first.
    baseline = _file(Line("cli untaped awx put", DeprecatedMark(13)))
    current = _file(Line("cli untaped awx put", DeprecatedMark(12)))
    assert changelog.postponement_facts(baseline, current) == []


FACT = changelog.Fact("report_error", "`report_error` will now be removed in 13.0 instead of 12.0.")
RELEASED = "# Changelog\n\n## 12.0.0rc1\n\n### Added\n\n- old\n"


def test_build_writes_a_fact_under_deprecated() -> None:
    built, _ = changelog.build_text(RELEASED, "12.0.0", {"added": ["- new"]}, [FACT])
    assert "### Deprecated\n\n- `report_error` will now be removed in 13.0 instead of 12.0.\n" in (
        built
    )


def test_an_entry_naming_the_thing_replaces_the_fact() -> None:
    reason = "- `report_error` stays until 13.0 so plugins keep a major to move."
    built, _ = changelog.build_text(RELEASED, "12.0.0", {"deprecated": [reason]}, [FACT])
    assert "will now be removed" not in built
    other = "- `report_errors` stays."
    built, _ = changelog.build_text(RELEASED, "12.0.0", {"deprecated": [other]}, [FACT])
    assert "will now be removed in 13.0" in built


def test_the_release_check_finds_a_dropped_fact() -> None:
    body = section("## 12.0.0\n\n### Deprecated\n\n- `other` goes.\n", "12.0.0")
    assert changelog.missing_facts(deprecated_entries(body), [FACT]) == [FACT]
    kept = section(f"## 12.0.0\n\n### Deprecated\n\n- {FACT.text}\n", "12.0.0")
    assert changelog.missing_facts(deprecated_entries(kept), [FACT]) == []


# --- the baseline ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _release(repo: Path, tag: str, *lines: Line) -> None:
    surface = repo / changelog.SURFACE_FILE
    surface.parent.mkdir(parents=True, exist_ok=True)
    surface.write_text(_file(*lines), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", tag)
    _git(repo, "tag", tag)


def test_the_baseline_is_the_newest_tag_release_candidates_included(repo: Path) -> None:
    line = "sdk untaped.sdk.report_error(exc)"
    _release(repo, "v11.3.0", Line(line, DeprecatedMark(12)))
    _release(repo, "v12.0.0rc1", Line(line, DeprecatedMark(13)))
    _release(repo, "v12.0.0.dev1", Line(line, DeprecatedMark(15)))  # never tagged by release.py
    (repo / changelog.SURFACE_FILE).write_text(
        _file(Line(line, DeprecatedMark(14))), encoding="utf-8"
    )

    (fact,) = changelog.surface_facts(repo)
    assert fact.text.endswith("removed in 14.0 instead of 13.0.")


def test_without_a_tag_the_build_names_the_fetch(repo: Path) -> None:
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    with pytest.raises(changelog.ChangelogError) as caught:
        changelog.surface_facts(repo)
    assert str(caught.value) == changelog.NO_TAG
    assert "git fetch --tags origin" in changelog.NO_TAG


@pytest.mark.parametrize(
    ("tags", "prereleases", "expected"),
    [
        (["v11.3.0", "v12.0.0rc1"], True, "v12.0.0rc1"),
        (["v11.3.0", "v12.0.0rc1"], False, "v11.3.0"),
        (["v11.3.0", "v12.0.0.dev1"], True, "v11.3.0"),
        (["nightly"], True, None),
    ],
)
def test_baseline_tag(tags: list[str], prereleases: bool, expected: str | None) -> None:
    assert release.baseline_tag(tags, prereleases=prereleases) == expected
