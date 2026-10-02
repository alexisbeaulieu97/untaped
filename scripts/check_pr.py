"""Checks on a pull request itself: its drift review and its changelog line.

Usage: ``uv run python scripts/check_pr.py EVENT_JSON``, where EVENT_JSON is
the ``pull_request`` event (``$GITHUB_EVENT_PATH`` in Actions). The PR's
changes are ``git diff BASE...HEAD`` in the current checkout, so CI checks it
out with full history.

- The body has a ``## Drift review`` section with a non-empty line for each of
  :data:`DRIFT_ITEMS` (the PR template's lines; the checklist itself lives in
  CONTRIBUTING.md, "Before you open a PR").
- A PR that changes shipped code (``packages/*/src/``) changes the
  ``## Unreleased`` section of CHANGELOG.md, or its ``Changelog:`` line opens
  with ``none`` and says why.

Every failure prints one line on stderr and exits 1.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from untaped.git import GitCommandError, run_git

DRIFT_HEADING = "## Drift review"
DRIFT_ITEMS = (
    "Docs, skills and READMEs",
    "Changelog",
    "Duplicated helpers",
    "Repo rules",
    "Issues",
)
_SHIPPED = re.compile(r"^packages/[^/]+/src/")
_UNRELEASED = re.compile(r"^## Unreleased[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def drift_review(body: str) -> dict[str, str] | None:
    """The ``- Item: answer`` lines of the body's drift review, or ``None`` without one."""
    text = _COMMENT.sub("", body.replace("\r\n", "\n"))
    match = re.search(rf"^{re.escape(DRIFT_HEADING)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        return None
    answers: dict[str, str] = {}
    for line in match.group(1).splitlines():
        item, colon, answer = line.strip().removeprefix("- ").partition(":")
        if colon:
            answers[item.strip()] = answer.strip()
    return answers


def unreleased(changelog: str) -> str:
    """The body of CHANGELOG.md's ``## Unreleased`` section (empty without one)."""
    match = _UNRELEASED.search(changelog)
    return match.group(1).strip() if match else ""


def problems(
    body: str, changed: Sequence[str], base_changelog: str, head_changelog: str
) -> list[str]:
    """Every reason the PR fails its checks (empty when it passes)."""
    answers = drift_review(body)
    if answers is None:
        return [f"the PR body has no '{DRIFT_HEADING}' section (see the PR template)"]
    found = [
        f"drift review: answer '{item}' (what you checked, or 'n/a')"
        for item in DRIFT_ITEMS
        if not answers.get(item)
    ]
    ships = any(_SHIPPED.match(path) for path in changed)
    logged = unreleased(base_changelog) != unreleased(head_changelog)
    waived = answers.get("Changelog", "").lower().startswith("none")
    if ships and not logged and not waived:
        found.append(
            "packages/*/src changed: add a CHANGELOG.md line under '## Unreleased', "
            "or answer 'Changelog: none, <why>'"
        )
    return found


def _git(*args: str) -> str:
    return run_git(list(args), timeout=60, capture=True).text


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        print("usage: check_pr.py EVENT_JSON", file=sys.stderr)
        return 2
    pull = json.loads(Path(argv[0]).read_text(encoding="utf-8"))["pull_request"]
    base = pull["base"]["sha"]
    try:
        changed = _git("diff", "--name-only", f"{base}...HEAD").split()
        base_changelog = _git("show", f"{base}:CHANGELOG.md")
    except GitCommandError as exc:
        print(f"check_pr: {exc}", file=sys.stderr)
        return 1
    head_changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    found = problems(pull.get("body") or "", changed, base_changelog, head_changelog)
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
