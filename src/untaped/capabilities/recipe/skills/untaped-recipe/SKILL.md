---
name: untaped-recipe
description: Use the `untaped recipe` command to apply reusable file recipes (templated files, YAML edits, copies, removals) across many directories or repos with a preview, backups and a CI drift check, and to author and test recipe packs. Use when the user mentions recipes, recipe packs, codemods, bulk or fleet-wide file changes, applying the same change to many repos, or drift checks.
---

# Untaped Recipe

Use this skill when applying reusable local file recipes across one or more
plain directories, or when authoring and testing recipe packs. The engine is
VCS-agnostic: it plans every change in memory, previews it, and only writes
after confirmation. Planning is the only execution — there are no shell steps,
no control flow in recipes, and no state or inventory.

Command tree: recipe verbs sit at `untaped recipe <verb>` (`apply`, `list`,
`get`, `edit`, `init`, `validate`, `test`); packs, hooks, and backups have
their own nouns: `recipe packs add|sync|list|get|edit|remove|init`,
`recipe hooks list|get|edit|init|run`, and `recipe backups
list|get|restore|prune`.

Details that do not fit here ship next to this file:

| File | Read it when |
|---|---|
| [references/apply.md](references/apply.md) | preview shapes, `--parallel`, input precedence and prompts, sensitive inputs, piped records, record kinds |
| [references/library.md](references/library.md) | installing, syncing, inspecting or removing packs, `validate`, golden `test` cases, the trust model, backups and restores |
| [references/authoring.md](references/authoring.md) | scaffolding packs, writing recipe YAML and hooks, `hooks run`, the built-in `yaml_edit` hook |

## Applying recipes

- `untaped recipe apply <recipe> <dir>...` plans, previews on stderr, confirms,
  backs up, then writes. The recipe argument is a bare name (unique across
  installed packs), a `pack/recipe` ref, an explicit path to a `recipe.yml`, or
  a local pack path plus `--recipe <name>`. An installed pack name or a local
  pack path with exactly one recipe selects that recipe; with several, the
  error lists them. A value is a path only when it is
  `.` or `..`, starts with `./`, `../`, `/`, or `~`, or ends in
  `.yml`/`.yaml` — anything else is a library ref, never probed on disk.
  The same target directory given twice (in any spelling) is planned once.
- Pass `--yes`/`-y` for non-interactive applies. Backups are on by default;
  use `--no-backup` only when the target tree is protected another way.
- `--dry-run` plans and previews without writing or creating backups. Pack
  hooks still execute for real during `--dry-run` (and `--check`): they
  compute the planned changes, so a dry run is not a way to inspect an
  untrusted pack.
- `--check` is the CI/drift mode: writes nothing, creates no backups, prompts
  for nothing, exits 3 when any target would change, and exits 1 when any
  target fails. Rows carry `action: planned` (or `unchanged`).
- Declining the confirmation exits 1 with `cancelled; no changes made`; rows
  for the targets that would have changed carry `action: cancelled`.
- `--quiet`/`-q` mutes post-run success chatter only — never preview detail,
  warnings, errors, or confirmation prompts.
- Failures are per-target: a target that fails to plan or write is reported and
  writes nothing, while other targets proceed. Within a target, writes are
  transactional and roll back on failure.

## Inputs

- Provide fixed values with repeated `--var KEY=VALUE` and repeated
  `--vars-file file.yml` YAML mappings: a later file wins over an earlier one,
  and `--var` wins over every file (the same flags and precedence as `awx
  test`). Unknown input names are rejected. `--vars-file` values are YAML
  (`3.10` → `3.1`, `on` → `true`): quote version-like strings.
- For inputs declared `list` or `dict`, `--var` parses the value as YAML first:
  `--var 'cols=[name, owner]'`, `--var 'labels={team: platform}'`. Scalar
  inputs keep literal-string semantics. `--vars-file` files may hold native lists
  and mappings.

- Agents pass every required input with `--var`/`--vars-file`, or add
  `--non-interactive`: without a terminal a missing required input fails
  instead of prompting.

## Pipes

- `apply --stdin` reads targets from stdin instead of positional dirs (never
  both). Lines are bare paths or untaped pipe records; lines that parse as JSON
  scalars (a directory named `2024`) are still paths — only JSON objects with
  the untaped envelope marker are records.
- With `--stdin`, the confirmation reads the controlling terminal; input
  prompts never run. Without a terminal, apply refuses before planning (exit
  2) unless `--yes`, `--dry-run`, or `--check` is given.
- Input `from` expressions can read the per-target pipe `record`, so upstream
  tool output can drive both target selection and input values.
- Prefer `--format json` for machine-readable summaries and `--format pipe`
  (NDJSON envelope `{"untaped":"1","kind":...,"record":...}`) when chaining
  into other untaped tools. A table shows each command's usual columns
  (`--columns ?` marks them, `--columns +inputs` adds one); json, yaml, raw
  and pipe keep every field. `--columns`/`-c` narrows row fields. `--format`
  and `--columns` affect stdout rows only, never the stderr preview.
- `recipe.apply_outcome` rows carry absolute `target_path`, `action`,
  `files_changed`, `warnings` (a list: accumulated `helpers.warn(...)`
  messages, skipped optional transforms, a skip reason), `detail` (the failure
  message; `null` unless failed; a failed row adds `error`: `category`,
  `system`, `retryable`, `message`, `hint`), resolved `inputs`, and `recipe` (canonical `pack/recipe`
  ref). Actions: `planned` (`--dry-run`/`--check` would change), `applied`,
  `unchanged` (plan produced no writes), `skipped` (validate hook returned
  `helpers.skip(...)`; not applicable, never a failure), `cancelled`
  (confirmation declined), and `failed`. Skips are success (all-skip runs
  exit 0, no backup); a skip is not `--check` drift. Summary lines gain a
  `N skipped` count.

## Safety

- Installing a pack is installing code: its hooks run as the user, with full
  file access, even during `--dry-run` and `--check`. Inspect an unfamiliar
  pack before installing it; see [references/library.md](references/library.md).
- Exit codes: 0 success, 1 failure (a bad recipe, pack or input, a missing
  name, a failing hook) or declined confirmation, 2 usage error, 3 `--check`
  drift, 4 fix the environment (`uv` missing, `$EDITOR`, settings), 5
  temporary (a git fetch timeout; retry later), 130 interrupted; a run exits
  with its most severe failure. With `--format json` stderr is JSON Lines
  whose errors carry `category`, `system`, `retryable` and `hint`.
