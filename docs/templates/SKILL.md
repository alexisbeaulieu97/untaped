---
name: untaped-CAPABILITY
description: Operates SYSTEM through the `untaped CAPABILITY` command (TASKS IN A FEW WORDS). Use when the user wants to INTENT, or mentions TRIGGER WORDS.
---

<!--
Template for a capability's packaged agent skill. Copy it to
src/untaped/capabilities/CAPABILITY/skills/untaped-CAPABILITY/SKILL.md (an
external provider ships it inside its own package), declare it in
CapabilitySpec.skills, replace every UPPER_CASE placeholder, and delete this
comment.

The principle: say only what the agent cannot learn from the installed CLI,
and make the risky paths hard to get wrong. `--help` and `--columns ?`
answer flags and fields; the skill says which commands form a workflow,
which order is safe, what the output means and what to do next. The rules
below are untaped's, on top of general skill-writing practice.

Frontmatter
- description equals SkillAsset.description (SPEC.description) exactly;
  tests/unit/test_skill_files.py compares them. It routes, it does not
  instruct: third person, what the skill covers, then the intents that
  should load it. Under 60 words, only this capability's ground, no ": ".
- name is the full ID, untaped-CAPABILITY. It stays stable: installs keep it
  in their directory and marker even when chosen by short selector.

Content
- The installed skill is the agent's whole manual. It never links to docs/
  or the source tree, which do not exist next to an installed CLI.
- It describes the version it ships with. Change it in the same PR as the
  command, setting or contract it describes; history goes in the CHANGELOG.
- Every quoted `untaped ...` command must parse against the real CLI (the
  same test). Write synopses as `[--flag VALUE]`, `a|b`, NAME or <name>.
- Exact commands only where a wrong flag is costly; elsewhere name the
  command and the intent.
- A destructive operation is a sequence: preview (--dry-run, --check, or
  list the selection), show the user what it will touch, scope it
  explicitly, wait for approval, then pass --yes. Say how to recover.
- Leave out `untaped skills ...` mechanics (the root's) and implementation
  notes, class names or test details.
- Examples use invented names (acme, Deploy, prod). Write calmly: a reason
  works better than capitals.

Shape
- SKILL.md holds what every use needs, in about 900 words. What only some
  uses reach goes in references/TOPIC.md, one level deep, each pointer
  saying when to read it. A reference over about 100 lines opens with a list
  of its contents. Sample input files go in examples/.
- Behaviour test cases live outside this directory: `skills install` copies
  the whole folder.
-->

# untaped CAPABILITY

## When to use

One or two sentences: the job this capability does, and when another
capability or tool fits better.

## Setup

Settings live under `profiles.<name>.CAPABILITY`. Set the token with
`untaped config set CAPABILITY.token --prompt` and check the connection with
`untaped CAPABILITY whoami`. Never print, echo or log tokens.

## Commands

| When you need to | Run |
|---|---|
| CONDITION | `untaped CAPABILITY NOUN list` |
| CONDITION | `untaped CAPABILITY NOUN get NAME` |
| CONDITION, after a preview | `untaped CAPABILITY NOUN delete NAME --dry-run` |

## Workflows

1. STEP, ending on something the agent can check.
2. Preview the change with `--dry-run` and show the user what it will touch.
3. After the user approves, rerun with `--yes`. Exit 1 with
   `cancelled; no changes made` means declined; other codes mean it failed.

Read `--format json` rather than table output. Exit codes: 0 success, 1
failure or declined, 2 usage (including a write without a terminal and
without `--yes`), 3 predicate hit, 4 fix the environment, 5 retry later.

## Pitfalls

- A LIMIT, SURPRISING DEFAULT OR COMMON MISTAKE, with the reason.

## References

- Read `references/TOPIC.md` when SITUATION.
