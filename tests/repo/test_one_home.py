"""Each fact has one home: no paragraph is copied, nearly word for word, into a second place.

Prose paragraphs and list items (outside code fences, tables and headings, at
least :data:`MIN_WORDS` words) of the docs, the root guides, the package
READMEs and the packaged skills are compared by their 5-word shingles, within
a page and across pages. Two fail when :data:`THRESHOLD` of the shorter one's
shingles appear in the other.

This catches near-verbatim copies only: a copied sentence inside otherwise
different paragraphs, a fact under :data:`MIN_WORDS` words, or a paraphrase
is still the reviewer's to catch.

A skill must stand alone for an agent that has only the installed CLI, so it
may restate docs; skill files are compared only with the same capability's
other skill files and README. Intentional README repeats go in
:data:`ALLOWED`, each with its reason.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from repo.support import FENCE, REPO_ROOT, markdown_files

MIN_WORDS = 20
THRESHOLD = 0.5
ALLOWED: dict[str, str] = {
    # Opening words of a package README paragraph -> why the repeat is intentional.
    "Install it as part of `untaped`": "each package README is a standalone PyPI page",
    "The [packaged skill]": "the standard Reference section of every package README",
}

_ITEM = re.compile(r"\n(?=\s*(?:[-*]|\d+\.) )")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_WORD = re.compile(r"[a-z0-9_./-]+")


def _pages() -> list[Path]:
    generated = REPO_ROOT / "docs/reference/config.md"
    return [page for page in markdown_files() if page != generated]


def _blocks(text: str) -> list[str]:
    """The prose paragraphs and list items of a Markdown page, headings dropped."""
    found = []
    for block in re.split(r"\n\s*\n", FENCE.sub("", text)):
        lines = block.strip().splitlines()
        while lines and lines[0].startswith("#"):
            lines.pop(0)
        found.extend(item.strip() for item in _ITEM.split("\n".join(lines)))
    return found


def paragraphs(text: str, *, allowed: tuple[str, ...] = ()) -> list[str]:
    """The blocks of a page worth comparing, skipping those opening with ``allowed``."""
    return [
        block
        for block in _blocks(text)
        if not block.startswith(("|", "<!--", *allowed)) and len(_words(block)) >= MIN_WORDS
    ]


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


def _comparable_paragraphs(page: Path) -> list[str]:
    allowed = tuple(ALLOWED) if _owner(page)[0] == "readme" else ()
    return paragraphs(page.read_text(encoding="utf-8"), allowed=allowed)


def repeats(blocks: list[str]) -> list[str]:
    """Every block of one page that a later block nearly copies."""
    return [a for a, b in itertools.combinations(blocks, 2) if overlap(a, b) >= THRESHOLD]


def copies(pages: list[Path]) -> list[str]:
    """Every pair of near-identical paragraphs, in one page or two."""
    texts = {page: _comparable_paragraphs(page) for page in pages}
    found = [
        f"{page.relative_to(REPO_ROOT)} (twice): {block[:70]!r}…"
        for page in pages
        for block in repeats(texts[page])
    ]
    for first, second in itertools.combinations(pages, 2):
        if not comparable(first, second):
            continue
        for a, b in itertools.product(texts[first], texts[second]):
            if overlap(a, b) >= THRESHOLD:
                where = f"{first.relative_to(REPO_ROOT)} and {second.relative_to(REPO_ROOT)}"
                found.append(f"{where}: {a[:70]!r}…")
    return found


def test_no_paragraph_is_copied() -> None:
    assert copies(_pages()) == [], "keep the fact in one home and link to it"


def test_every_allowed_repeat_still_occurs() -> None:
    readmes = [page.read_text(encoding="utf-8") for page in _pages() if _owner(page)[0] == "readme"]
    for opening in ALLOWED:
        assert any(f"\n{opening}" in text for text in readmes), f"stale ALLOWED entry: {opening}"


SENTENCE = (
    "A write is destructive when it can replace or remove what an issue holds now, "
    "such as a transition or a patch that sets a field, and it previews before it asks."
)
SAME_TOPIC = (
    "A write is safe when it only adds to what an issue holds, such as a comment or a "
    "link, so it runs without a preview and never asks before it changes the issue."
)


@pytest.mark.parametrize(
    ("a", "b", "copied"),
    [
        (SENTENCE, SENTENCE.replace("now, such", "today, such"), True),
        (SENTENCE, SAME_TOPIC, False),
    ],
)
def test_overlap_detector(a: str, b: str, copied: bool) -> None:
    assert (overlap(a, b) >= THRESHOLD) is copied


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
    assert paragraphs(text, allowed=tuple(ALLOWED)) == []
    assert len(paragraphs(text)) == 1


def test_paragraphs_split_lists_and_drop_headings() -> None:
    item = "- " + "word " * 25
    assert paragraphs(f"## Title\n{item}\n{item}\n- short") == [item.strip(), item.strip()]


def test_repeats_within_one_page() -> None:
    assert repeats(paragraphs(f"{SENTENCE}\n\n{SAME_TOPIC}\n\n{SENTENCE}\n")) == [SENTENCE]
