"""Checks on a pull request itself: its drift review and its changelog fragment.

Usage: ``uv run python scripts/check_pr.py EVENT_JSON``, where EVENT_JSON is
the ``pull_request`` event (``$GITHUB_EVENT_PATH`` in Actions). The PR's
changes are measured against the base it will merge into: the first parent of
the merge commit Actions checks out (current ``main``, not the possibly stale
``base.sha`` of the event), or the merge base with ``base.sha`` when HEAD is
not a merge. CI checks out with full history.

- The body has a ``## Drift review`` section with a non-empty line for each of
  :data:`DRIFT_ITEMS` (the PR template's lines; the checklist itself lives in
  CONTRIBUTING.md, "Before you open a PR").
- A PR that changes shipped code (``packages/*/src/``) adds or changes a
  fragment in ``changelog.d/``, or its ``Changelog:`` line reads
  ``none, <why>``.
- A fragment that is ``**Breaking`` comes with an ``upgrading`` fragment.
- Only a release PR (one that changes the ``untaped`` package version) edits
  ``CHANGELOG.md`` or ``changelog/``, and it leaves no fragment behind.

Every failure prints one line on stderr and exits 1.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

import changelog
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
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_PACKAGE = "packages/untaped/pyproject.toml"
_WAIVER = re.compile(r"none\b\W+\w", re.IGNORECASE)


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


def problems(
    body: str,
    changed: Sequence[str],
    fragments: Mapping[str, str],
    *,
    release: bool = False,
    leftover: Sequence[str] = (),
) -> list[str]:
    """Every reason the PR fails its checks (empty when it passes).

    ``fragments`` maps each fragment the PR adds or changes to its text; ``leftover`` lists the
    fragments still in ``changelog.d/`` at the PR's head; ``release`` says it is a release PR.
    """
    answers = drift_review(body)
    if answers is None:
        return [f"the PR body has no '{DRIFT_HEADING}' section (see the PR template)"]
    found = [
        f"drift review: answer '{item}' (what you checked, or 'n/a')"
        for item in DRIFT_ITEMS
        if not answers.get(item)
    ]
    ships = any(_SHIPPED.match(path) for path in changed)
    waived = _WAIVER.match(answers.get("Changelog", "")) is not None
    if ships and not fragments and not waived:
        found.append(
            "packages/*/src changed: add a fragment changelog.d/<slug>.<type>.md "
            "(see CONTRIBUTING.md, Changelog), or answer 'Changelog: none, <why>'"
        )
    breaking = [path for path, text in fragments.items() if changelog.is_breaking(text)]
    upgrading = any(changelog.fragment_type(path) == "upgrading" for path in fragments)
    if breaking and not upgrading:
        found.append(
            f"{breaking[0]} is Breaking: add changelog.d/<slug>.upgrading.md saying what "
            "a user or script must do about it"
        )
    edited = [p for p in changed if p == "CHANGELOG.md" or p.startswith("changelog/")]
    if edited and not release:
        found.append(
            f"{edited[0]} changed outside a release PR: add a fragment in changelog.d/ instead"
        )
    if release and leftover:
        found.append(
            f"{leftover[0]} is still a fragment in this release PR: "
            "run `uv run python scripts/changelog.py build X.Y.Z` again"
        )
    return found


def _package_version(pyproject: str) -> str:
    return str(tomllib.loads(pyproject)["project"]["version"])


def _git(*args: str) -> str:
    return run_git(list(args), timeout=60, capture=True).text


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        print("usage: check_pr.py EVENT_JSON", file=sys.stderr)
        return 2
    pull = json.loads(Path(argv[0]).read_text(encoding="utf-8"))["pull_request"]
    try:
        merge = len(_git("rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
        base = "HEAD^1" if merge else _git("merge-base", pull["base"]["sha"], "HEAD").strip()
        changed = _git("diff", "--name-only", base, "HEAD").split()
        base_version = _package_version(_git("show", f"{base}:{_PACKAGE}"))
    except GitCommandError as exc:
        print(f"check_pr: {exc}", file=sys.stderr)
        return 1
    head_version = _package_version(Path(_PACKAGE).read_text(encoding="utf-8"))
    fragments = {
        path: Path(path).read_text(encoding="utf-8")
        for path in changed
        if changelog.fragment_type(path) and Path(path).is_file()
    }
    found = problems(
        pull.get("body") or "",
        changed,
        fragments,
        release=head_version != base_version,
        leftover=changelog.fragment_paths(Path()),
    )
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
