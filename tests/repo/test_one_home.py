"""Each fact has one home: no paragraph is copied, nearly word for word, into a second file.

Prose paragraphs (outside code fences, tables and headings, at least
:data:`MIN_WORDS` words) of the docs, the root guides, the package READMEs
and the packaged skills are compared by their 5-word shingles. Two paragraphs
in different files fail when :data:`THRESHOLD` of the shorter one's shingles
appear in the other.

A skill must stand alone for an agent that has only the installed CLI, so it
may restate docs; skill files are compared only with the same capability's
other skill files and README. Intentional repeats go in :data:`ALLOWED`, each
with its reason.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from repo.support import REPO_ROOT

MIN_WORDS = 20
THRESHOLD = 0.5
ALLOWED: dict[str, str] = {
    # Opening words of the paragraph -> why the repeat is intentional.
    "Install it as part of `untaped`": "each package README is a standalone PyPI page",
    "The [packaged skill]": "the standard Reference section of every package README",
}

_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1", re.MULTILINE | re.DOTALL)
_ITEM = re.compile(r"\n(?=\s*(?:[-*]|\d+\.) )")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_WORD = re.compile(r"[a-z0-9_./-]+")


def _pages() -> list[Path]:
    generated = REPO_ROOT / "docs/reference/config.md"
    docs = [p for p in sorted((REPO_ROOT / "docs").rglob("*.md")) if p != generated]
    root = [REPO_ROOT / name for name in ("README.md", "CONTRIBUTING.md", "AGENTS.md")]
    readmes = sorted(REPO_ROOT.glob("packages/*/README.md"))
    skills = sorted(REPO_ROOT.glob("packages/*/src/*/skills/**/*.md"))
    return [*docs, *root, *readmes, *skills]


def paragraphs(text: str) -> list[str]:
    """The prose paragraphs, and list items, of a Markdown page worth comparing."""
    found = []
    for block in re.split(r"\n\s*\n", _FENCE.sub("", text)):
        lines = block.strip().splitlines()
        while lines and lines[0].startswith("#"):
            lines.pop(0)
        for item in _ITEM.split("\n".join(lines)):
            item = item.strip()
            if item.startswith(("|", "<!--")) or item.startswith(tuple(ALLOWED)):
                continue
            if len(_words(item)) >= MIN_WORDS:
                found.append(item)
    return found


def _words(block: str) -> list[str]:
    return _WORD.findall(_LINK_TARGET.sub("]", block).lower())


def _shingles(block: str) -> set[tuple[str, ...]]:
    words = _words(block)
    return {tuple(words[i : i + 5]) for i in range(len(words) - 4)}


def overlap(a: str, b: str) -> float:
    """The share of the shorter paragraph's shingles found in the other."""
    first, second = _shingles(a), _shingles(b)
    return len(first & second) / min(len(first), len(second)) if first and second else 0.0


def _owner(path: Path) -> tuple[str, str | None]:
    """``("skill"|"readme"|"doc", capability)`` for a page."""
    parts = path.relative_to(REPO_ROOT).parts
    if "skills" in parts:
        return "skill", parts[1]
    if parts[0] == "packages":
        return "readme", parts[1]
    return "doc", None


def comparable(first: Path, second: Path) -> bool:
    """Skill files meet only their own capability's skill files and README."""
    (kind_a, cap_a), (kind_b, cap_b) = _owner(first), _owner(second)
    if "skill" not in (kind_a, kind_b):
        return True
    return cap_a == cap_b and {kind_a, kind_b} <= {"skill", "readme"}


def copies(pages: list[Path]) -> list[str]:
    """Every pair of near-identical paragraphs in two different pages."""
    texts = {page: paragraphs(page.read_text(encoding="utf-8")) for page in pages}
    found = []
    for first, second in itertools.combinations(pages, 2):
        if not comparable(first, second):
            continue
        for a, b in itertools.product(texts[first], texts[second]):
            if overlap(a, b) >= THRESHOLD:
                where = f"{first.relative_to(REPO_ROOT)} and {second.relative_to(REPO_ROOT)}"
                found.append(f"{where}: {a[:70]!r}…")
    return found


def test_no_paragraph_is_copied_into_a_second_file() -> None:
    assert copies(_pages()) == [], "keep the fact in one home and link to it"


SENTENCE = (
    "A write is destructive when it can replace or remove what an issue holds now, "
    "such as a transition or a patch that sets a field, and it previews before it asks."
)


@pytest.mark.parametrize(
    ("a", "b", "copied"),
    [
        (SENTENCE, SENTENCE.replace("now, such", "today, such"), True),
        (SENTENCE, "Search issues with JQL and get one row per issue, " * 3, False),
    ],
)
def test_overlap_detector(a: str, b: str, copied: bool) -> None:
    assert (overlap(a, b) >= THRESHOLD) is copied


def test_skills_meet_only_their_own_capability() -> None:
    skill = REPO_ROOT / "packages/untaped-awx/src/untaped_awx/skills/untaped-awx/SKILL.md"
    assert comparable(skill, REPO_ROOT / "packages/untaped-awx/README.md")
    assert not comparable(skill, REPO_ROOT / "packages/untaped-jira/README.md")
    assert not comparable(skill, REPO_ROOT / "docs/scripting.md")
    assert comparable(REPO_ROOT / "docs/scripting.md", REPO_ROOT / "README.md")


def test_paragraphs_skip_code_tables_and_allowed_repeats() -> None:
    text = (
        "```bash\n"
        + "untaped x " * 30
        + "\n```\n\n| a | b |\n\nInstall it as part of `untaped`: "
        + "w " * 30
        + "\n\n  ```\n"
        + "indented code " * 20
        + "\n  ```"
    )
    assert paragraphs(text) == []


def test_paragraphs_split_lists_and_drop_headings() -> None:
    item = "- " + "word " * 25
    assert paragraphs(f"## Title\n{item}\n{item}\n- short") == [item.strip(), item.strip()]
