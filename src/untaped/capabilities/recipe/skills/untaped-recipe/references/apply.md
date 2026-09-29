# Apply details: previews, inputs and pipe records

The finer points of `untaped recipe apply`: preview shapes, concurrency, input resolution and prompting, sensitive inputs, how piped records become targets, and the record kinds every recipe command emits.

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
- Records resolve absolute `record.target_path` first, then generic
  `record.path`. Records whose `kind` ends in `.summary` are skipped as
  non-targets. Repo-grain records such as `workspace.repo` must provide
  `target_path`; records without it are rejected before planning.
- Emit kinds: `apply` → `recipe.apply_outcome` (one row per target);
  `validate` → `recipe.check`; `test` → `recipe.test`; `list`/`get` →
  `recipe.recipe`; `packs list`/`packs get` → `recipe.pack`; `hooks
  list`/`hooks get` → `recipe.hook`; `hooks run` → `recipe.hook_run`; `packs
  add` → `recipe.add_outcome`; `packs sync` → `recipe.sync_outcome`; `packs
  remove` → `recipe.remove_outcome`; `backups` → `recipe.backup`.
