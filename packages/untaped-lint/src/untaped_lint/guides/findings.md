---
description: "Read when a run reports findings or one must be left out: rule ids, targets, ignores and allow markers."
---

## Rule ids

A finding's `rule` is its public id, `<key>.<id>`: the key is the plugin that
declares the rule (`lint` for lint's own) or the repository's own key (`name:`
in `.untaped/lint.yml`, as `house`), and the id is what the rule declares
(`error-case`). The bare id is accepted wherever exactly one loaded rule
carries it; a bare id two keys declare names neither. `untaped lint rules`
lists every loaded rule with what it reads, its severity and its summary.

A finding's `target` is what it is about, spelled by its owner: a file's path,
a plugin's name. Its `location`, when it has one, is the file and line.

## Leaving a finding out

A suppression is `<rule>[:<target-glob>]`, spelled the same in each place it
can be written, and the places add up:

| Where | For |
|---|---|
| `lint.ignore` (a list) in `config.yml` | every run (in `profiles.<name>`, every run in that profile) |
| `ignore:` in `.untaped/lint.yml` | every run in that repository |
| `--ignore` (repeatable) | one run |

In the glob `*` stops at `/` and `**` crosses it. The line a finding points
at may carry `# untaped: allow <rule>` (the bare id or `<key>.<id>`), which
allows that rule there; a bare id two keys declare allows nothing, with a
warning. An `ignore:` or `lint.ignore` entry, or a marker, hides the finding
from every later run; `--ignore` hides it from one. An id no loaded rule
declares is a warning, not an error: the plugin that owns it may not be
installed, or its key may be missing from the lint file. `untaped doctor`
checks the profile's list (`lint.ignore-ids`).

## What narrowing never hides

`--select` and PATHS narrow what is reported, never what is read. They never
drop `lint.plugin-not-installed` or `lint.quarantined`, which say no rule ran
on a plugin, or `lint.unreadable-file`, which says a file was not read; an
ignore that names one of these rules does drop it. A rule or provider that
raises is a failure on stderr for that rule (exit 1); the other rules still
run, and a failed rule is never a pass.
