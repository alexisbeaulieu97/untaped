# Writing rules

A convention the user explains once becomes a rule every agent after you
runs. A rule reads one subject (the repository's files, a plugin, a
plugin's records) and yields findings.

## The loop

1. Say the convention in one sentence: it is the rule's `summary`, what the
   next agent reads in `untaped lint rules`.
2. `untaped lint new <id>` (kebab-case, as `no-print`). It writes
   `.untaped/lint/<id>.py` and its test, and `.untaped/lint.yml` when the
   repository has none (`--key` names its key then, as `--key house`).
3. Write the examples first: in `tests/lint/test_<id>.py`, the smallest
   `BAD` files that break the convention and `GOOD` ones that follow it.
   Without pytest in the repository, they are the files in
   `.untaped/lint/fixtures/<id>/bad/` and `good/` instead.
4. Run the test: the bad example fails, because the rule is still empty.
5. Write the rule until the test passes.
6. Run `untaped lint` at the root: fix what it finds in the code, or narrow
   the rule if it flags what the convention allows.
7. Commit the rule and its test together, and delete the prose the rule
   replaced.

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
  and `json()`, `yaml()`, `python()` for the parsed file. `*` stops at `/`,
  `**` crosses it. A file that isn't UTF-8 never reaches the rule; lint
  reports it as `lint.unreadable-file`.
- `target_of(file)` is the file's path, which `ignore:` globs match.
- The id is bare: the run prefixes the key, so the finding reads
  `house.no-print`.
- A rule that raises is a failure row for that rule; the others still run.

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

## What a rule module may import

The standard library, `untaped_lint.api`, and an installed plugin's
`untaped_<plugin>.api` (a rule over that plugin's records). Nothing else:
the rule runs in untaped's environment, not the repository's, so a library
that works on your machine fails on the next agent's. `lint.rule-imports`
reports it. A rule that needs a library is a plugin
(`untaped plugin new`).

## The lint file

```yaml
version: 1
name: house                  # the key of the repository's own rules
exclude: ["vendor/**"]       # left out of the files, beyond what git ignores
linters:
  house:
    rules: .untaped/lint     # the *.py files directly in it
readers:
  awx: ["specs/**/*.yml"]    # an installed plugin reads these into its records
ignore:
  - house.no-print:scripts/**
```

A key is spelled like a plugin name and is never `untaped` or an installed
plugin's name. `fixtures/` under a rules directory is never linted.
