---
name: untaped-CAPABILITY
description: Use the `untaped CAPABILITY` command to TASK. Use when the user mentions TRIGGER WORDS (product names, nouns and verbs a user would say).
---

<!--
Skill template for a capability's packaged agent skill.

Copy this file to src/<package>/skills/untaped-CAPABILITY/SKILL.md (built-ins:
src/untaped/capabilities/CAPABILITY/skills/untaped-CAPABILITY/SKILL.md), declare
it in CapabilitySpec.skills, and replace every UPPER_CASE placeholder.

Rules:
- The installed skill is the agent's whole manual: an agent that has only
  the installed CLI must be able to do the job with it. Never link to docs/
  or the source repository, which do not exist next to an installed CLI.
- SKILL.md is about 900 words: the map (when to use what, the main loop,
  pitfalls). Details go in references/TOPIC.md files beside it, linked by
  relative path; sample input files go in examples/.
- Write for an agent operating the CLI: commands, flags, outputs, safety.
  No implementation notes, class names, test details or release history.
- Every command and flag must exist in `untaped CAPABILITY ... --help`.
  tests/unit/test_skill_files.py parses every quoted `untaped ...` command
  against the real CLI; write synopses as `[--flag VALUE]`, `a|b`, NAME or
  <name> placeholders.
- The description is what makes an agent load the skill: name the task and
  the words a user would use (for example "job template", "inventory sync").
  Keep it identical to SkillAsset.description and free of ": " (YAML).
- Delete this comment.
-->

# untaped CAPABILITY

One or two sentences: what this capability does and when to use it rather
than another tool.

Details: `references/TOPIC.md` (only when this page would pass ~900 words).

## Setup

- The command ships with `untaped`; there is nothing else to install.
- Settings (under `profiles.<name>.CAPABILITY`):

  | Setting | Purpose |
  |---|---|
  | `CAPABILITY.base_url` | ... |
  | `CAPABILITY.token` | Secret. Set with `untaped config set CAPABILITY.token --prompt`. |

- Check the connection with `untaped CAPABILITY whoami` (or `ping`).
- Never print, echo or log tokens.

## Commands

| Task | Command |
|---|---|
| ... | `untaped CAPABILITY NOUN list` |
| ... | `untaped CAPABILITY NOUN get NAME` |
| ... | `untaped CAPABILITY NOUN create ... --dry-run` |

Run `untaped CAPABILITY --help` to confirm options before acting.

## Output and pipes

- Use `--format json` to read results; do not parse table output.
- stdout carries data only; progress, warnings and errors go to stderr.
- Record kinds: `CAPABILITY.NOUN` from `list`/`get`, `CAPABILITY.VERB_outcome`
  from writes.
- `--stdin` on `COMMAND` reads NAMES, or `CAPABILITY.NOUN` records from
  `--format pipe`.

## Safety

- Commands that change things preview first. Run them with `--dry-run`, show
  the user the preview, and pass `--yes` only after the user approves.
- Without a terminal, a write with neither `--yes` nor `--dry-run` exits 2.
- Exit codes: 0 success, 1 failure or declined, 2 usage error, 3 predicate
  hit (if the capability has `--check`-style flags), 130 interrupted.

## Pitfalls

- LIMITS, RATE LIMITS, SURPRISING DEFAULTS, COMMON MISTAKES.

## Examples

```bash
untaped CAPABILITY NOUN list --format json
untaped CAPABILITY NOUN list --format pipe | untaped CAPABILITY NOUN get --stdin
```
