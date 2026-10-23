"""A deprecation's sentence comes from its mark, never typed by hand: a postponement can't go stale.

Plugins get ``lint.hand-typed-deprecation`` through ``untaped lint``; core is
no plugin, so this runs lint's messages family over core's source (its message
builders and its marks aside, which are where the sentences are made). The
prose of the docs names no removal version either: a postponement would leave
it behind. Fenced examples, the changelog's history and the generated config
reference (regenerated, so never stale) are exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

from repo.support import FENCE, PACKAGES, REPO_ROOT, markdown_files
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions.messages import message_violations

CORE = PACKAGES / "untaped" / "src" / "untaped"
#: Where core builds the sentences: the message helpers and the marks.
BUILDERS = frozenset({CORE / "sdk" / "words.py", CORE / "sdk" / "marks.py"})
#: A removal version in prose.
VERSION_IN_PROSE = re.compile(r"\b(?:removed in|until) \d+\.0\b")
#: History and generated pages, relative to the repo root.
EXEMPT_DOCS = ("CHANGELOG.md", "changelog.d/", "changelog/", "docs/reference/config.md")


def core_violations(source_dir: Path = CORE) -> list[str]:
    """``hand-typed-deprecation`` findings in core's source, the builders aside."""
    files = [file for file in source_files(source_dir) if file.path not in BUILDERS]
    found = message_violations(source_dir, files)
    return [line for line in found if "::hand-typed-deprecation::" in line]


def prose_versions(text: str) -> list[str]:
    """The lines of Markdown ``text`` outside fenced blocks that name a removal version."""
    prose = FENCE.sub("", text)
    return [line.strip() for line in prose.splitlines() if VERSION_IN_PROSE.search(line)]


def test_core_types_no_deprecation_sentence() -> None:
    found = core_violations()
    assert not found, (
        "every deprecation is a promise with a date, so its sentence comes from its mark "
        "(deprecated(removed_in=…), deprecated_alias(…, removed_in=…), Renamed(…, removed_in=…), "
        "deprecated_behaviour(…, removed_in=…)) or untaped.sdk.words:\n"
        + "\n".join(f"  {line}" for line in found)
    )


def test_the_docs_name_no_removal_version_in_prose() -> None:
    found = [
        f"{path.relative_to(REPO_ROOT)}: {line}"
        for path in markdown_files()
        if not str(path.relative_to(REPO_ROOT)).startswith(EXEMPT_DOCS)
        for line in prose_versions(path.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "a removal version belongs in the mark, which warnings and help render; say "
        "'until the major its warning names' instead:\n" + "\n".join(f"  {line}" for line in found)
    )


def test_a_planted_sentence_fails(tmp_path: Path) -> None:
    package = tmp_path / "untaped"
    package.mkdir()
    (package / "m.py").write_text('NOTE = "x is deprecated and will be removed in 12.0"\n')
    (package / "messages.py").write_text("")
    assert core_violations(package) == [
        "untaped/m.py::hand-typed-deprecation::x is deprecated and will be removed in 12.0: "
        "build it from the mark: deprecated(removed_in=…), deprecated_alias(…, removed_in=…), "
        "Renamed(…, removed_in=…), deprecated_behaviour(…, removed_in=…)"
    ]


def test_a_planted_prose_line_fails_and_a_fenced_one_passes() -> None:
    text = (
        "Intro.\n\n```console\nwarning: x is deprecated and will be removed in 12.0\n```\n\n"
        "It is kept until 12.0.\n"
    )
    assert prose_versions(text) == ["It is kept until 12.0."]


def test_core_allows_no_hand_typed_sentence() -> None:
    marker = "untaped: allow hand-typed-deprecation"
    found = [
        str(file.path.relative_to(REPO_ROOT))
        for file in source_files(CORE)
        if any(marker in line for line in file.lines)
    ]
    assert not found, (
        "core builds every deprecation sentence from a mark, so it needs no allow marker; "
        "a behaviour uses deprecated_behaviour(...).sentence or .notify:\n"
        + "\n".join(f"  {path}" for path in found)
    )
