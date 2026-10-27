---
description: "Read before turning a convention into a repository rule: how a rule comes to be, a rule and its test."
---

A rule reads one subject (the repository's files, a
plugin, a plugin's records) and yields findings.

## How a rule comes to be

1. The convention, said in one sentence, is the rule's `summary`: what
   `untaped lint rules` shows the next agent.
2. `untaped lint new ID` (a kebab-case id, as `no-print`) writes the rule
   module and its test, and the lint file when the repository has none
   (`untaped lint new --help` says where each goes).
3. The test holds the smallest `BAD` files that break the convention and
   `GOOD` ones that follow it; it fails until the rule is written.
4. `untaped lint` at the root then reports what the rule finds in the code.

## A rule

```python
# .untaped/lint/no_print.py
"""house.no-print: library code writes through the CLI's output, never print()."""

from collections.abc import Iterator

from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of


@rule("no-print", reads=Tree, severity="error", summary="Library code never calls print().")
def no_print(tree: Tree) -> Iterator[Finding]:
    for file in tree.files("src/**/*.py"):
        for number, line in enumerate(file.lines, start=1):
            if "print(" in line:
                yield finding(
                    no_print,
                    target_of(file),
                    "print() in library code; return the value instead",
                    location=FileLocation(path=file.path, line=number),
                )
```

- `reads=Tree` takes the model, never a kind string.
- `tree.files(*globs)` yields each matching file: `path`, `text`, `lines`,
  and `json()`, `yaml()`, `python()` for the parsed file; in a glob `*` stops
  at `/` and `**` crosses it. A file that isn't
  UTF-8 never reaches the rule; lint reports it as `lint.unreadable-file`.
- `target_of(file)` is the file's path, which an ignore glob matches.
- The id is bare: the run prefixes the key, so the finding reads
  `house.no-print`.

## Its test

```python
# tests/lint/test_no_print.py
from pathlib import Path

from untaped_lint.testing import repo_rule, run_rule, tree

RULE = repo_rule("no-print", root=Path(__file__).resolve().parents[2])

BAD = {"src/a.py": "print(1)\n"}
GOOD = {"src/a.py": "x = 1\n", "scripts/b.py": "print(1)\n"}


def test_no_print_flags_the_bad_example() -> None:
    assert [each.rule for each in run_rule(RULE, tree(BAD))] == ["no-print"]


def test_no_print_passes_the_good_example() -> None:
    assert run_rule(RULE, tree(GOOD)) == []
```

What a rule module may import, and what loading it runs:
`untaped docs lint guides lint-file`.
