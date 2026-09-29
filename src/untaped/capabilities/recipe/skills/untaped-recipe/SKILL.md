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
- Preview goes to stderr; stdout carries only data rows. Normal apply and
  `--dry-run` default to `--preview table` (changed files, absolute paths,
  change kind, line counts); `--check` defaults to summary-only. `--preview
  diff` gives patch-compatible unified diffs; `--preview none` gives the
  summary line only. Large plans collapse from per-file to per-target rows at
  the `recipe.preview_max_rows` setting (default 50, `0` = unlimited), then
  truncate with an exact `showing first N of M targets` count — collapsed
  previews are summaries backed by exact totals, and `--preview diff` is the
  full-detail escape.
- `--parallel N` plans targets concurrently (clamped, max 32) and sizes the
  per-hook-project worker pool. `--hook-timeout SECONDS` overrides the per-hook
  request timeout (`0` disables); worker environment startup runs under the
  separate `recipe.hook_startup_timeout_seconds` bound (default 300) with a
  `preparing hook environment...` stderr notice.
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
- `--input-from NAME='<jinja>'` overrides a per-target derivation source. It
  uses the same sandbox as recipe `from` (literal text, constants, and field
  access on `target.path`/`target.name`/`target.parent_path`/
  `target.parent_name`/`record` only — no filters, operators, calls, or
  control blocks) and must resolve for every target, unlike recipe `from`
  candidates which fall through silently.
- Precedence per input: fixed value or source override → recipe `from` →
  recipe default → prompt (required inputs only) → `missing required input`
  error.
  Combining `--var`/`--vars-file` with `--input-from` for one input is a usage
  error. `scope: global` inputs reject `--input-from` but accept `--var`.
  A `default:` must coerce to the input's `type` (checked at load, so `validate`
  reports it) and cannot be combined with `required: true`.
- A required input with no value is prompted for only when stdin is a
  terminal (sensitive inputs as a hidden secret); optional and defaulted
  inputs are never prompted, and structured (`list`/`dict`) inputs never are.
  Without a TTY (piped `--stdin` targets included), or with
  `--non-interactive` or `--check`, the input fails with `missing required input: NAME;
  pass --var NAME=VALUE or --vars-file FILE` (a global input fails the run, a
  target input fails that target's row). Prompts run serially in target
  order before planning starts (planning stays parallel with `-j`), and
  Ctrl-C at a prompt aborts the run with exit 130. Agents should pass every
  required input explicitly or use `--non-interactive`.
- Sensitive inputs render as `***` in rows, warnings, errors, and backup
  metadata, and file-level preview detail and diffs are suppressed for targets
  that resolve a sensitive input (not overridable by `--preview diff`). Real
  values still reach templates and hooks.

## Pipes

- `apply --stdin` reads targets from stdin instead of positional dirs (never
  both). Lines are bare paths or untaped pipe records; lines that parse as JSON
  scalars (a directory named `2024`) are still paths — only JSON objects with
  the untaped envelope marker are records.
- Records resolve absolute `record.target_path` first, then generic
  `record.path`. Records whose `kind` ends in `.summary` are skipped as
  non-targets. Repo-grain records such as `workspace.repo` must provide
  `target_path`; records without it are rejected before planning.
- With `--stdin`, the confirmation reads the controlling terminal; input
  prompts never run. Without a terminal, apply refuses before planning (exit
  2) unless `--yes`, `--dry-run`, or `--check` is given.
- Input `from` expressions can read the per-target pipe `record`, so upstream
  tool output can drive both target selection and input values.
- Prefer `--format json` for machine-readable summaries and `--format pipe`
  (NDJSON envelope `{"untaped":"1","kind":...,"record":...}`) when chaining
  into other untaped tools. `--columns`/`-c` narrows row fields. `--format`
  and `--columns` affect stdout rows only, never the stderr preview.
- Emit kinds: `apply` → `recipe.apply_outcome` (one row per target);
  `validate` → `recipe.check`; `test` → `recipe.test`; `list`/`get` →
  `recipe.recipe`; `packs list`/`packs get` → `recipe.pack`; `hooks
  list`/`hooks get` → `recipe.hook`; `hooks run` → `recipe.hook_run`; `packs
  add` → `recipe.add_outcome`; `packs sync` → `recipe.sync_outcome`; `packs
  remove` → `recipe.remove_outcome`; `backups` → `recipe.backup`.
- `recipe.apply_outcome` rows carry absolute `target_path`, `action`,
  `files_changed`, `warnings` (a list: accumulated `helpers.warn(...)`
  messages, skipped optional transforms, a skip reason), `error` (`null`
  unless failed), resolved `inputs`, and `recipe` (canonical `pack/recipe`
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
- Exit codes: 0 success, 1 failure or declined confirmation, 2 usage error,
  3 `--check` drift, 130 interrupted.
