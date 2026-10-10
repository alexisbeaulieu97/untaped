# Writing a plugin's skill

A plugin ships its agent skill as a directory holding `SKILL.md`, declared
on `PluginSpec.skills` (see [Packaged skills](./plugins.md#packaged-skills)).
This page is the template and the rules for what goes in it.

Start from this template. Copy it to
`src/<package>/skills/untaped-<plugin>/SKILL.md`, replace every
UPPER_CASE placeholder, and declare it in `PluginSpec.skills`:

```markdown
---
name: untaped-PLUGIN
description: Operates SYSTEM through the `untaped PLUGIN` command (TASKS IN A FEW WORDS). Use when the user wants to INTENT, or mentions TRIGGER WORDS.
---

# untaped PLUGIN

One or two sentences: the job this plugin does, the judgement it needs,
and when another plugin or tool fits better.

## Setup

Settings live under `profiles.<name>.PLUGIN`. The user stores the token
by running `untaped auth set PLUGIN` in their own terminal (the settings
model needs a `token_command` field for that). Check the connection with
`untaped PLUGIN whoami`. Never ask for, print, echo or log tokens.

## Commands

| When you need to | Run |
|---|---|
| CONDITION | `untaped PLUGIN NOUN list` |
| CONDITION | `untaped PLUGIN NOUN get NAME` |
| CONDITION, after a preview | `untaped PLUGIN NOUN delete NAME --dry-run` |

## Workflows

1. STEP, ending on something the agent can check.
2. Preview the change with `--dry-run` and show the user what it will touch.
3. After the user approves, rerun with `--yes`.

## Safety

- WHICH COMMANDS WRITE, which ask first, and how to preview each.
- Exit codes: 0 success, 1 failure or declined (`cancelled; no changes
  made`), 2 usage (including a write without a terminal and without
  `--yes`), 3 predicate hit, 4 fix the environment, 5 retry later, 130
  interrupted.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- A LIMIT, SURPRISING DEFAULT OR COMMON MISTAKE, with the reason.

## References

| File | Read it when |
|---|---|
| [references/TOPIC.md](references/TOPIC.md) | SITUATION |
```

Composition requires only a non-empty skill name and description; the rest
of this section is guidance, and no test checks the description's length or
voice. Keep `SKILL.md` short and split a long reference by task. The content
rule: a skill documents behaviour and judgement, not what the CLI prints. Say only what the agent cannot learn from the installed CLI,
and make the risky paths hard to get wrong.
`--help` and `--columns '?'` answer flags and fields; the skill says which
commands form a workflow, which order is safe, what the output means and what
to do next.

Frontmatter:

- `description` equals `SkillAsset.description` exactly. It routes rather
  than instructs: in the third person, what the skill covers, then the
  intents that should load it. Aim for under 60 words, on this plugin's
  ground only, and without `": "`.
- `name` is the full ID, `untaped-<plugin>`.

Content:

- The installed skill is the agent's whole manual. It never links to the
  repository's docs or source tree, which do not exist next to an installed
  CLI.
- It describes the version it ships with. Change it in the same release as
  the command, setting or contract it describes; history goes in the
  changelog.
- Every quoted `untaped ...` command should parse against the real CLI.
  Write synopses as `[--flag VALUE]`, `a|b`, `NAME` or `<name>`.
- Give exact commands only where a wrong flag is costly; elsewhere name the
  command and the intent.
- A destructive operation is a sequence: preview (`--dry-run`, `--check`, or
  list the selection), show the user what it will touch, scope it
  explicitly, wait for approval, then pass `--yes`. Say how to recover.
- Leave out `untaped skills ...` mechanics (the root's) and implementation
  notes, class names or test details.
- Examples use invented names (acme, Deploy, prod). Write calmly: a reason
  works better than capitals.

Shape:

- `SKILL.md` holds what every use needs, in about 900 words. What only some
  uses reach goes in `references/TOPIC.md`, one level deep, each pointer
  saying when to read it. A reference over about 100 lines opens with a list
  of its contents. Sample input files go in `examples/`.
- Keep behaviour test cases outside the skill directory, because
  `skills install` copies the whole folder. Rerun them when a change could
  alter what an agent does.
