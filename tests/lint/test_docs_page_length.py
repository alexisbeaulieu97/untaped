"""house.docs-page-length: the bad example breaks the rule and the good one doesn't."""

from pathlib import Path

from untaped_lint.testing import repo_rule, run_rule, tree

RULE = repo_rule("docs-page-length", root=Path(__file__).resolve().parents[2])

BAD = {"docs/long.md": "line\n" * 401}
GOOD = {"docs/long.md": "line\n" * 400, "README.md": "line\n" * 401}


def test_docs_page_length_flags_the_bad_example() -> None:
    found = run_rule(RULE, tree(BAD))
    assert [(each.rule, each.target, each.message) for each in found] == [
        ("docs-page-length", "docs/long.md", "401 lines; split the page by reader task")
    ]


def test_docs_page_length_passes_the_good_example() -> None:
    assert run_rule(RULE, tree(GOOD)) == []
