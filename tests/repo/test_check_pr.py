"""scripts/check_pr.py: the drift review and changelog checks on a pull request."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import check_pr
import pytest

from repo.support import REPO_ROOT

FILLED = """\
Before: x. After: y.

## Drift review

<!-- One line each. -->

- Docs, skills and READMEs: README updated
- Changelog: added under Fixed
- Duplicated helpers: none found
- Repo rules: n/a
- Issues: Closes #12
"""
FRAGMENT = "changelog.d/new-thing.fixed.md"
FRAGMENTS = {FRAGMENT: "Something is fixed.\n"}
BREAKING = {"changelog.d/gone.removed.md": "**Breaking (awx):** `x` is removed.\n"}
UPGRADING = {"changelog.d/gone.upgrading.md": "awx: replace `x` with `y`.\n"}
PYPROJECT = '[project]\nname = "untaped"\nversion = "10.0.0"\n'
SRC = ["packages/untaped-awx/src/untaped_awx/cli/commands.py"]


def _answer(body: str, item: str, answer: str) -> str:
    return re.sub(rf"^- {re.escape(item)}:.*$", f"- {item}: {answer}", body, flags=re.M)


def test_a_filled_review_with_a_changelog_line_passes() -> None:
    assert check_pr.problems(FILLED, SRC, FRAGMENTS) == []


def test_the_template_fails_until_it_is_filled() -> None:
    template = (REPO_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert check_pr.drift_review(template) == dict.fromkeys(check_pr.DRIFT_ITEMS, "")
    assert len(check_pr.problems(template, [], {})) == len(check_pr.DRIFT_ITEMS)


def test_a_body_without_the_section_fails() -> None:
    assert check_pr.problems("Before: x.", [], {}) == [
        "the PR body has no '## Drift review' section (see the PR template)"
    ]


def test_a_commented_out_answer_is_empty() -> None:
    body = _answer(FILLED, "Repo rules", "<!-- todo -->")
    assert check_pr.problems(body, [], {}) == [
        "drift review: answer 'Repo rules' (what you checked, or 'n/a')"
    ]


@pytest.mark.parametrize(
    ("changed", "fragments", "changelog_answer", "fails"),
    [
        (SRC, {}, "added", True),
        (SRC, {}, "none, internal refactor", False),
        (SRC, {}, "None: tests only", False),
        (SRC, {}, "none", True),
        (SRC, {}, "nonetheless added", True),
        (SRC, FRAGMENTS, "added", False),
        (["docs/scripting.md", "packages/untaped-awx/tests/x.py"], {}, "n/a", False),
    ],
)
def test_shipped_code_needs_a_fragment_or_a_reason(
    changed: list[str], fragments: dict[str, str], changelog_answer: str, fails: bool
) -> None:
    body = _answer(FILLED, "Changelog", changelog_answer)
    found = check_pr.problems(body, changed, fragments)
    assert bool(found) is fails
    assert all("changelog.d/" in problem for problem in found)


def test_a_breaking_fragment_needs_an_upgrading_fragment() -> None:
    (problem,) = check_pr.problems(FILLED, [], BREAKING)
    assert "gone.removed.md is Breaking" in problem
    assert ".upgrading.md" in problem
    assert check_pr.problems(FILLED, [], {**BREAKING, **UPGRADING}) == []
    assert check_pr.problems(FILLED, [], FRAGMENTS) == []


def test_only_a_release_pr_edits_the_changelog() -> None:
    for path in ("CHANGELOG.md", "changelog/9.x.md"):
        (problem,) = check_pr.problems(FILLED, [path], {})
        assert path in problem
        assert "add a fragment in changelog.d/ instead" in problem
        assert check_pr.problems(FILLED, [path], {}, release=True) == []


def test_the_pr_that_moves_unreleased_into_fragments_may_edit_the_changelog() -> None:
    assert check_pr.problems(FILLED, ["CHANGELOG.md"], {}, migration=True) == []


def test_a_release_pr_leaves_no_fragment_behind() -> None:
    (problem,) = check_pr.problems(FILLED, [], {}, release=True, leftover=[FRAGMENT])
    assert FRAGMENT in problem
    assert "changelog.py build" in problem
    assert check_pr.problems(FILLED, [], {}, leftover=[FRAGMENT]) == []


def test_contributing_lists_the_template_items() -> None:
    """The checklist in CONTRIBUTING.md and the template's lines name the same items."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = text.split("## Before you open a PR", 1)[1].split("\n## ", 1)[0]
    assert re.findall(r"^- \*\*([^*]+)\.\*\*", section, re.M) == list(check_pr.DRIFT_ITEMS)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_main_reads_the_event_and_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    (repo / "packages/untaped-x/src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write(repo / "packages/untaped/pyproject.toml", PYPROJECT)
    _write(repo / "CHANGELOG.md", "# Changelog\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _rev(repo, "HEAD")
    (repo / "packages/untaped-x/src/m.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "body": FILLED}}))
    monkeypatch.chdir(repo)

    assert check_pr.main([str(event)]) == 1
    assert "changelog.d/" in capsys.readouterr().err

    _write(repo / FRAGMENT, "Something is fixed.\n")
    _commit(repo, "fragment")
    assert check_pr.main([str(event)]) == 0

    _write(repo / "packages/untaped/pyproject.toml", PYPROJECT.replace("10.0.0", "10.1.0"))
    _write(repo / "CHANGELOG.md", "# Changelog\n")
    _commit(repo, "release")
    assert check_pr.main([str(event)]) == 1  # a release PR with a fragment left over
    assert "changelog.py build" in capsys.readouterr().err
    (repo / FRAGMENT).unlink()
    _commit(repo, "built")
    waived = _answer(FILLED, "Changelog", "none, the release")
    event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "body": waived}}))
    assert check_pr.main([str(event)]) == 0


def test_main_measures_a_merge_commit_against_its_first_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main moved after the event's base.sha: its fragment is not this PR's."""
    repo = tmp_path / "repo"
    (repo / "packages/untaped-x/src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write(repo / "packages/untaped/pyproject.toml", PYPROJECT)
    _write(repo / "CHANGELOG.md", "# Changelog\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    stale = _rev(repo, "HEAD")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "packages/untaped-x/src/m.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    _git(repo, "checkout", "-q", "main")
    _write(repo / FRAGMENT, "Another PR's fragment.\n")  # lands on main
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "other")
    _git(repo, "merge", "-q", "--no-edit", "pr")  # the merge ref Actions checks out
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"base": {"sha": stale}, "body": FILLED}}))
    monkeypatch.chdir(repo)

    assert check_pr.main([str(event)]) == 1


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rev(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
