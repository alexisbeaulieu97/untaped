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
BASE_LOG = "# Changelog\n\n## 10.0.0\n\n- old\n"
HEAD_LOG = "# Changelog\n\n## Unreleased\n\n### Fixed\n\n- new\n\n## 10.0.0\n\n- old\n"
SRC = ["packages/untaped-awx/src/untaped_awx/cli/commands.py"]


def _answer(body: str, item: str, answer: str) -> str:
    return re.sub(rf"^- {re.escape(item)}:.*$", f"- {item}: {answer}", body, flags=re.M)


def test_a_filled_review_with_a_changelog_line_passes() -> None:
    assert check_pr.problems(FILLED, SRC, BASE_LOG, HEAD_LOG) == []


def test_the_template_fails_until_it_is_filled() -> None:
    template = (REPO_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert check_pr.drift_review(template) == dict.fromkeys(check_pr.DRIFT_ITEMS, "")
    assert len(check_pr.problems(template, [], BASE_LOG, BASE_LOG)) == len(check_pr.DRIFT_ITEMS)


def test_a_body_without_the_section_fails() -> None:
    assert check_pr.problems("Before: x.", [], BASE_LOG, BASE_LOG) == [
        "the PR body has no '## Drift review' section (see the PR template)"
    ]


def test_a_commented_out_answer_is_empty() -> None:
    body = _answer(FILLED, "Repo rules", "<!-- todo -->")
    assert check_pr.problems(body, [], BASE_LOG, BASE_LOG) == [
        "drift review: answer 'Repo rules' (what you checked, or 'n/a')"
    ]


@pytest.mark.parametrize(
    ("changed", "head", "changelog_answer", "fails"),
    [
        (SRC, BASE_LOG, "added", True),
        (SRC, BASE_LOG, "none, internal refactor", False),
        (SRC, BASE_LOG, "None: tests only", False),
        (SRC, HEAD_LOG, "added", False),
        (["docs/scripting.md", "packages/untaped-awx/tests/x.py"], BASE_LOG, "n/a", False),
        # A line outside ``## Unreleased`` does not count.
        (SRC, BASE_LOG.replace("- old", "- old\n- new"), "added", True),
    ],
)
def test_shipped_code_needs_a_changelog_line_or_a_reason(
    changed: list[str], head: str, changelog_answer: str, fails: bool
) -> None:
    body = _answer(FILLED, "Changelog", changelog_answer)
    found = check_pr.problems(body, changed, BASE_LOG, head)
    assert bool(found) is fails
    assert all("CHANGELOG.md" in problem for problem in found)


def test_unreleased_reads_only_its_own_section() -> None:
    assert check_pr.unreleased(HEAD_LOG) == "### Fixed\n\n- new"
    assert check_pr.unreleased(BASE_LOG) == ""


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
    (repo / "CHANGELOG.md").write_text(BASE_LOG)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "packages/untaped-x/src/m.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "body": FILLED}}))
    monkeypatch.chdir(repo)

    assert check_pr.main([str(event)]) == 1
    assert "CHANGELOG.md" in capsys.readouterr().err

    (repo / "CHANGELOG.md").write_text(HEAD_LOG)
    assert check_pr.main([str(event)]) == 0
