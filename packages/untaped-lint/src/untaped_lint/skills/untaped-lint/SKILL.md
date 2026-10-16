---
name: untaped-lint
description: Runs a repository's conventions as rules through `untaped lint` (its `.untaped/lint.yml` rules, each plugin's, and the plugin checks), lists them with `untaped lint rules`, and turns a convention written in prose into a rule with `untaped lint new`. Use before changing code in a repository that has `.untaped/lint.yml` or untaped plugins, when `untaped lint` fails, or when the user explains a convention to keep.
---

# untaped lint

`untaped lint` runs rules over subjects and reports findings, each with a
rule id such as `house.no-print` or `lint.error-case`: `<key>.<id>`, where
the key is the plugin that declares the rule or the repository's own key
(`name:` in `.untaped/lint.yml`). In a plugin repository each plugin its
`pyproject.toml` declares is checked against the house conventions and
its contract fills; in any repository its files are checked against every
rule that reads them, the repository's own rules in `.untaped/lint/`
included.

## Setup

- Install the plugin with `untaped[lint]` (it is experimental).
- Install the plugin you check in the same environment, as with
  `uv pip install -e .`; a declared plugin that is not installed is the
  `lint.plugin-not-installed` finding.
- A repository's rules live beside `.untaped/lint.yml` at its root; a rule
  module runs in untaped's process, as `conftest.py` runs under pytest, so
  read a repository's `.untaped/lint/` before you lint it.
- `profiles.<name>.lint.ignore` lists findings every run leaves out, each
  `<rule>[:<target-glob>]`.

## Commands

| When | Command |
|---|---|
| See the conventions before changing code | `untaped lint rules` |
| Check the repository you are in | `untaped lint` |
| Check only some files' findings | `untaped lint 'src/**/cli/*.py'` |
| Run one rule | `untaped lint --select house.no-print` |
| Leave a rule out for one run | `untaped lint --ignore 'house.no-print:tests/**'` |
| Fail on warnings too | `untaped lint --strict` |
| Start a rule for a new convention | `untaped lint new no-print` |

## Workflows

1. Before changing code, run `untaped lint rules`: that table, not a
   document, is the list of conventions, each with its one-sentence summary.
2. After changing code, run `untaped lint`. A finding names its rule, its
   target and, where it has one, the file and line. Copy the `rule`,
   `target` and `location` from the finding; never compose an id.
3. Exit 3 means fix the code, or ask the user. Never edit `ignore:` or
   `lint.ignore` to go green. Mark a line `# untaped: allow <id>` only where
   the code must do the flagged thing.
4. When the user explains a convention to keep, make it a rule; the loop is
   in [writing rules](references/writing-rules.md): say it as the rule's
   one-sentence summary, `untaped lint new <id>`, write the bad and good
   examples first, then the rule, run `untaped lint`, commit the rule and
   its test, and delete the prose it replaced.
5. To check every installed plugin, pipe their records in:

   ```sh
   untaped plugin list -f pipe | untaped lint --stdin
   ```

## Safety

- `untaped lint` and `untaped lint rules` read files and compose plugins;
  they change nothing. `untaped lint new` writes the rule, its test and the
  lint file, which `--dry-run` previews.
- A rule module runs as you, in-process; a module may import only the
  standard library, `untaped_lint.api` and an installed plugin's
  `untaped_<plugin>.api` (`lint.rule-imports`).
- A failure line (`error: <key>'s rules did not run`) means a rule or
  provider could not check: the run exits 1 and that check is not a pass.

## Pitfalls

- Exit 3 is "a rule found an error", exit 1 "a rule could not run",
  exit 2 a usage mistake or a lint file key that could shadow a plugin,
  exit 4 a configuration problem such as a rule importing a plugin that is
  not installed.
- `--ignore` and `ignore:` match a finding's target: a file's path, a
  plugin's name. To narrow by file for one run, pass PATHS.
- A bare id in a marker or `--select` names a rule only when exactly one
  loaded rule has it; write `<key>.<id>` otherwise.
- An id nobody declares is a warning, not an error: check the plugin that
  owns it is installed, or its key is in `.untaped/lint.yml`.

## References

- [Writing rules](references/writing-rules.md): the loop, a complete rule
  and its test, the repo file's keys.
- `untaped lint --help`, `untaped lint rules --help` and
  `untaped lint new --help` list the options.
