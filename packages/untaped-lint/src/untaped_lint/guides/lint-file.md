---
description: "Read before writing or editing .untaped/lint.yml: its keys, where it is found, and what loading its rules runs."
---

A repository's own rules are named by `.untaped/lint.yml` at its root:

```yaml
version: 1
name: house                  # the key of the repository's own rules: house.no-print
exclude: ["vendor/**"]       # left out of the files, on top of what git ignores
linters:
  house:
    rules: .untaped/lint     # a directory: each *.py directly in it
readers:
  awx: ["specs/**/*.yml"]    # an installed plugin reads these files into its records
ignore:
  - house.no-print:scripts/**
```

- `untaped lint` finds the file walking up from the current directory to the
  checkout root (the nearest directory holding `.git`). `--config PATH` reads
  another file and lints the current directory with its rules.
- `name:` and each `linters:` key are spelled like a plugin name and are never
  `untaped` or an installed plugin's name; such a key exits 2. A `readers:`
  key names an installed plugin.
- The files linted are what git tracks plus untracked files git doesn't
  ignore, so a new file is linted before `git add`; outside a checkout, every
  file under the root. `fixtures/` under a rules directory is never linted.
- A `readers:` key no installed plugin fills exits 4.

## What loading the rules runs

A rule module is Python that `untaped lint` imports in its own process, as
pytest imports a `conftest.py`: no sandbox and no confirmation, so a
repository's `.untaped/lint/` runs as you when you lint that checkout. A
module may import only the standard library, `untaped_lint.api` and an
installed plugin's `untaped_<plugin>.api`, because it runs in untaped's
environment, not the repository's (`lint.rule-imports` reports anything
else). Importing a plugin that isn't installed exits 4 naming the extra to
install. A rule that needs another library belongs in a plugin
(`untaped plugin new`).
