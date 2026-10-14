---
name: untaped-lint
description: Checks an untaped plugin against the house conventions and its contract fills through `untaped lint`, lists the rules with `untaped lint rules`, and reads or suppresses findings by rule id. Use when the user writes or reviews an untaped plugin, asks why `untaped lint` failed, or wants a rule ignored.
---

# untaped lint

`untaped lint` runs rules over subjects and reports findings, each with a
rule id such as `lint.error-case`. In a plugin repository the subject is
each plugin its `pyproject.toml` declares: core's checks of the composed
plugin (command grammar, marks, names, imports, contract fills) and the
rules that read its files (stderr wording, layering, structure). Other
plugins add rules of their own; their ids start with the plugin's name.

## Setup

- Install the plugin with `untaped[lint]` (it is experimental).
- Install the plugin you check in the same environment, as with
  `uv pip install -e .`; a declared plugin that is not installed is the
  `lint.plugin-not-installed` finding.
- `profiles.<name>.lint.ignore` lists findings every run leaves out, each
  `<rule>[:<target-glob>]`.

## Commands

| When | Command |
|---|---|
| Check the plugin repository you are in | `untaped lint` |
| Check only some files' findings | `untaped lint 'src/**/cli/*.py'` |
| Run one rule | `untaped lint --select lint.error-case` |
| Leave a rule out for one run | `untaped lint --ignore lint.print:untaped_acme/cli/**` |
| Fail on warnings too | `untaped lint --strict` |
| See every rule and what it reads | `untaped lint rules` |

## Workflows

1. A finding names its rule, the file and line where it has one, and the
   plugin. Fix the code; `untaped lint rules` gives the rule's summary.
2. When a source line breaks a rule on purpose, mark that line
   `# untaped: allow <rule>` (the bare id, or `lint.<rule>`), where the
   rule allows it (the stability, command and panel rules don't).
3. To check every installed plugin, pipe their records in:

   ```sh
   untaped plugin list -f pipe | untaped lint --stdin
   ```

4. In a plugin's tests, `untaped_lint.testing.assert_clean("<name>")` fails
   where `untaped lint` would exit non-zero, so CI catches what it would.

## Safety

- `untaped lint` reads files and composes plugins; it changes nothing.
- A failure line (`error: <plugin>'s rules did not run`) means a rule or
  provider could not check: the run exits 1 and that check is not a pass.

## Pitfalls

- Exit 3 is "a rule found an error", exit 1 "a rule could not run",
  exit 2 a usage mistake, exit 4 a configuration problem.
- `--ignore` and `lint.ignore` match the target with a glob where `*`
  stops at `/` and `**` crosses it.
- An id nobody declares is a warning, not an error: check the plugin that
  owns it is installed.

## References

- `untaped lint --help` and `untaped lint rules --help` list the options.
- untaped's Lint guide, in its documentation, explains ids, ignores and
  exit codes.
