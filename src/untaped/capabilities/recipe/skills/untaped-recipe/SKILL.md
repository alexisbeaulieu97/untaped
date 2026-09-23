---
name: untaped-recipe
description: Use the untaped recipe capability to apply local recipe packs across directories.
---

# Untaped Recipe

Use this skill when applying reusable local file recipes across one or more
plain directories, or when authoring and testing recipe packs. The engine is
VCS-agnostic: it plans every change in memory, previews it, and only writes
after confirmation. Planning is the only execution — there are no shell steps,
no control flow in recipes, and no state or inventory.

## Applying recipes

- `untaped recipe apply <recipe> <dir>...` plans, previews on stderr, confirms,
  backs up, then writes. The recipe argument is a bare name (unique across
  installed packs), a `pack/recipe` ref, an explicit path to a `recipe.yml`, or
  a local pack path plus `--recipe <name>`. A value is a path only when it is
  `.` or `..`, starts with `./`, `../`, `/`, or `~`, or ends in
  `.yml`/`.yaml` — anything else is a library ref, never probed on disk.
  The same target directory given twice (in any spelling) is planned once.
- Pass `--yes`/`-y` for non-interactive applies. Backups are on by default;
  use `--no-backup` only when the target tree is protected another way.
- `--dry-run` plans and previews without writing or creating backups.
- `--check` is the CI/drift mode: writes nothing, creates no backups, prompts
  for nothing, exits 3 when any target would change, and exits 1 when any
  target fails. Rows carry `action: planned` (or `unchanged`). Combining it
  with `--interactive` is a usage error (exit 2).
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

- Provide fixed values with repeated `--var KEY=VALUE` or a `--vars-file file.yml`
  YAML mapping (`--var` wins on conflict). Unknown input names are rejected.
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
  `--interactive` prompt → recipe default → `missing required input` error.
  Combining `--var`/`--vars-file` with `--input-from` for one input is a usage
  error. `scope: global` inputs reject `--input-from` but accept `--var`.
  A `default:` must coerce to the input's `type` (checked at load, so `validate`
  reports it) and cannot be combined with `required: true`.
- `--interactive` prompts for unresolved inputs (empty answer accepts the
  default; sensitive defaults are hidden but an empty answer still accepts).
  Structured (`list`/`dict`) inputs cannot be prompted — pass `--var`/`--vars-file`.
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
- With `--stdin`, the confirmation and `--interactive` prompts read the
  controlling terminal. Without one, apply refuses before planning (exit 2)
  unless `--yes`, `--dry-run`, or `--check` is given.
- Input `from` expressions can read the per-target pipe `record`, so upstream
  tool output can drive both target selection and input values.
- Prefer `--format json` for machine-readable summaries and `--format pipe`
  (NDJSON envelope `{"untaped":"1","kind":...,"record":...}`) when chaining
  into other untaped tools. `--columns`/`-c` narrows row fields. `--format`
  and `--columns` affect stdout rows only, never the stderr preview.
- Emit kinds: `apply` → `recipe.apply_outcome` (one row per target);
  `validate` → `recipe.check`; `test` → `recipe.test`; `hook run` →
  `recipe.hook_run`; `list`/`get` → `recipe.recipe`, `recipe.hook`,
  `recipe.pack`; `add` → `recipe.add_outcome`; `remove` →
  `recipe.remove_outcome`; `backup` → `recipe.backup`.
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

## Library and packs

- `add <path|git-url>` installs a pack and prints its recipes and hooks on
  stderr; it never prompts. `--rev` picks a git revision (git URL sources
  only), `--name` overrides the installed key (the pack identity everywhere).
  The row's `action` is `created`, or `updated` for a `--force` reinstall.
  The pack must load and contain a `uv.lock`. Reinstalling needs `--force`, which still refuses to
  overwrite a library copy with local edits unless `--discard-edits` is added.
- `list [--packs|--hooks]`, `get <ref>`, `edit <ref>`, `remove <pack>` operate
  on the unified library. `list --hooks` and `get` cover built-ins such as
  `yaml_edit` (marked `(builtin)`; not editable). `remove` is destructive,
  requires confirmation or `--yes` (`--dry-run` previews), exits 1 on a
  declined prompt, and warns when the copy has local edits.
- `validate [ref|path]` is static preflight: no ref validates the whole library
  and `packs.toml`; a ref validates one pack, recipe, path, or built-in. It
  AST-scans hook modules without importing them, and for hook-declaring
  projects requires `uv.lock` and verifies freshness with `uv lock --check`
  (hookless packs and recipe projects are exempt). Every persisted `packs.toml`
  row must include its `content_hash`; malformed or incomplete rows fail closed
  before a library mutation. An installed pack whose `pyproject.toml` cannot be
  parsed gets an error row in `validate`, is skipped with a warning by `list`, and
  is ignored by resolution unless named explicitly (then its error is shown).
  Template/copy sources containing `{{ input }}` tokens are only checked up to
  their literal directory prefix.
- `test [pack|path|pack/recipe]` runs golden-fixture cases under
  `tests/<recipe>/<case>/`: `given/` is copied to a temp target, `expected/` is
  the full expected tree (omitted = asserts no changes), optional data-only
  `case.yml` supplies `inputs`, `expect: success|error`, `error_contains`, and
  `verdict` assertions. `--update` regenerates `expected/` for an explicit pack
  or recipe. Exits non-zero on fail/error, including "no test cases found" for
  an explicit ref.

## Authoring packs

- `init pack <name>`, `init recipe <pack>/<recipe>`, `init hook <pack>/<hook>`
  scaffold pack projects; explicit local paths like `init hook ./my-pack/probe`
  target `./my-pack`. `init recipe` also scaffolds a starter golden case;
  `init hook` writes a typed stub plus a direct-call pytest (naming the kind and
  `--kind` on success), and packs ship `pytest` with `pythonpath = ["src"]` so
  `uv run --project <pack> pytest` works immediately. `init hook --kind X --force`
  replaces both the stub and the paired pytest (e.g. to fix a wrong `--kind`);
  without `--force` an existing hook is refused.
- Scaffolding refreshes the pack `uv.lock` and needs package-index access (or a
  `[tool.uv.sources]` override). If `uv lock` fails after files are written,
  the scaffold stays in place with a repairable error; `--no-lock` skips
  locking, but hooks cannot run until `uv lock` succeeds because workers use
  `uv run --locked --no-dev`.
- Recipe YAML is behavior-only: `version: 1`, optional `description`, optional
  `inputs`, and `steps`; `name:` is rejected. Step types are `validate`,
  `transform`, `template`, `copy`, and `remove`. `transform`/`remove` take
  exactly one of `file`, `files` (load-time fan-out to per-file steps), or
  `globs` (planning-time discovery; `exclude` skips matches; no implicit
  excludes, so repo sweeps usually add `exclude: [".git/**"]`; binary files
  must be excluded; matches under symlinked directories are skipped with a
  warning). `optional: true` (transform with `file`/`files` only)
  skips missing files with a warning. `template`/`copy` accept
  `if_absent: true` to create only when the destination does not exist.
- Template bodies render `{{ name }}` tokens from inputs, strict by default;
  `unknown_tokens: keep` preserves foreign tokens (GitHub Actions, Helm) while
  still rendering known inputs. Path-bearing fields (template/dest, source,
  file/files/globs/exclude) also render bare tokens — always strict, re-checked
  as confined relative paths after rendering. Sensitive and structured inputs
  are forbidden in path fields; derive a scalar input with `from` instead.
- Hook `args` pass verbatim — the engine never templates them; hooks read
  resolved `inputs` natively (structured inputs as real lists/dicts) and call
  `helpers.render_template()` themselves for templated string args. Use YAML
  anchors for structural reuse in recipes.
- A hook module exports `transform()`, `validate()`, or both — the exported
  name is the contract; manifest rows declare only `module`. Keep `untaped`
  as a dev-only dependency (scaffolding pins `>=<installed>,<next major>`);
  runtime hook dependencies go in `[project].dependencies`.
- Hook refs in a pack's recipes: a bare name resolves to the pack's own hook,
  else a built-in — never to another installed pack. Reference another pack's
  hook as `pack/hook`. (Only recipes without a project, and `hook run` without
  `--project`, look bare names up across installed packs.)
  Hooks must stay pure at planning time: read only the target tree and their
  own pack, never write or reach the network.
- Validate verdicts are `helpers.pass_()`, `helpers.fail(msg)`, and
  `helpers.skip(msg)` (not applicable → target `skipped`, never a failure).
  `helpers.warn(msg)` is a warning accumulator callable any number of times from
  validate and transform hooks; warnings attach to the target plan and do not
  replace the verdict. Call it for its side effect, then return a pass, fail, or
  skip verdict. `None` remains an implicit pass and a plain string is a fail
  message; unknown verdict objects and status values are rejected.
- `hook run <ref> --target DIR` debugs one hook without a recipe: transforms
  need `--file` (stdout is exact transformed content, or `--diff`); validates
  emit a `recipe.hook_run` verdict (`pass`/`fail`/`skip`) and exit 1 only
  on `fail`; a hook that raises prints its traceback as an `error:` and exits 1. The ref accepts the `./pack/hook` path form (resolves as
  `--project ./pack` + hook name; combining with explicit `--project` is a usage
  error). A local hook project is used only when named with `--project PATH`
  or a `./path` ref — never adopted implicitly from the current directory, so
  running inside a cloned repo does not execute its hooks.
  `--content`/`--content-file` supply fixture content; `--inputs`/
  `--args` load YAML fixture files and repeated `--input`/`--arg` KEY=VALUE
  overrides are YAML-parsed. Context echo (including fixture values) and
  accumulated warnings go to stderr — use `--quiet` in shared terminals when
  values are sensitive.
- For common YAML edits use the built-in `yaml_edit` transform hook: `edits`
  with `op: set|merge|delete|ensure`, paths of mapping keys, `{index: N}`, or
  first-match `{where: {...}}` selectors; string values render `{{ input }}`
  tokens and honor args-level `unknown_tokens: keep`. `ensure` idempotently adds
  a value if absent (list membership by `match` keys / equality, or mapping
  set-if-absent). Every op leaves the file byte-identical when nothing
  changes (`set`/`merge` to the value already present are no-ops).

## Backups and safety

- Every apply creates one backup bundle by default. `backup list|get|restore
  <id>|prune` manage bundles; `get`/`restore` accept full ids, unambiguous
  prefixes, or `latest`. `restore` and `prune` take `--dry-run`. Restore
  previews and confirms like apply, applies the
  whole bundle as one transaction, and refuses to overwrite files changed after
  the backup unless `--force` is passed. Backups store text content only; mode
  and mtime are not preserved. `prune [--keep N] [--older-than DAYS]` falls
  back to the `recipe.backup_keep`/`recipe.backup_max_age_days` settings.
- All recipe-local and target-relative paths must be safe relative paths:
  absolute paths, `..` segments, and symlink traversal are rejected before any
  engine-mediated read or write, again after path-field rendering.
- Installing a pack is installing code (same trust model as `pip install`, no
  sandbox). Evaluate before trusting: the `add` summary, `get`, `validate`'s
  no-import scan, and the golden test harness.
- Run `untaped skills install --all` (or `untaped skills install untaped-recipe`)
  to install this packaged skill.
- Old spellings (`check`, `show`, `new`, `backup show`, `apply --vars`) still
  work with a deprecation warning until 7.0.
