"""house.docs-page-length: a docs page is at most 400 lines, so it stays readable by task."""

from collections.abc import Iterator

from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of

BUDGET = 400


@rule(
    "docs-page-length",
    reads=Tree,
    severity="error",
    summary=f"A docs page is at most {BUDGET} lines; an over-budget page is split, not exempted.",
)
def docs_page_length(tree: Tree) -> Iterator[Finding]:
    """One finding per page under docs/ longer than the budget."""
    for file in tree.files("docs/**/*.md"):
        if (count := len(file.lines)) > BUDGET:
            yield finding(
                docs_page_length,
                target_of(file),
                f"{count} lines; split the page by reader task",
                location=FileLocation(path=file.path, line=1),
            )
