# Recipe library, packs and backups

## Packs

- `packs add <path|git-url>` installs a pack and prints its recipes and hooks on
  stderr; it never prompts. `--rev` picks a git revision (git URL sources
  only), `--name` overrides the installed key (the pack identity everywhere).
  The row's `action` is `created`, or `updated` for a `--force` reinstall.
  The pack must load, contain a `uv.lock`, and contain no symlinks (outside
  ignored dirs such as `.venv`). Reinstalling needs `--force`, which still refuses to
  overwrite a library copy with local edits unless `--discard-edits` is added.
  A local path source is recorded as an absolute path; a git source records
  the requested `rev` and the resolved `commit` (shown in `packs list` and
  the `add`/`sync`/`remove` rows; `sync` updates it even when no file changed).
- `packs sync <pack>...` or `packs sync --all` re-fetches each installed pack from its
  recorded source and `--rev` (a branch or tag moves forward). Packs whose
  content would change are listed on stderr with the commit move
  (`old -> new`) and the hook-code files that change (`src/`, root `*.py`,
  `pyproject.toml`, `uv.lock`, `uv.toml`, `.python-version`, `setup.cfg`;
  not recipe files or tests), and need confirmation or `--yes` (`--dry-run` previews); rows
  carry `action` `updated`, `unchanged` or `planned`. A pack with local edits in the library fails unless
  `--discard-edits` is passed; a failed pack prints `error: PACK: ...` and a
  `failed` row with `detail` and `error`, the others still sync, and the
  command exits 1 (5 when a fetch timed out).
- Each noun reads and edits only its own kind: `list`/`get <recipe>`/`edit
  <recipe>` for recipes, `packs list`/`packs get <pack>`/`packs edit <pack>`
  (opens `pyproject.toml`) for packs, and `hooks list`/`hooks get
  <hook>`/`hooks edit <hook>` for hooks. `hooks list` and `hooks get` cover
  built-ins such as `yaml_edit` (marked `(builtin)`; not editable). `packs
  remove <pack>...` is destructive, requires confirmation or `--yes`
  (`--dry-run` previews), exits 1 on a declined prompt, and warns when the
  copy has local edits; a pack it cannot delete is a `failed` row. `packs sync` and `packs remove` take `--stdin`
  (pack names or `recipe.pack` records, e.g. `packs list --format pipe`).
  `get`/`edit` on a pack or hook name, and `init NAME` without `/`, fail
  with a hint naming the `packs`/`hooks` command.
- `validate [ref|path]` is static preflight: no ref validates the whole library
  and `packs.toml`; a ref validates one pack, recipe, path, or built-in. It
  AST-scans hook modules without importing them, and for hook-declaring
  projects requires `uv.lock` and verifies freshness with `uv lock --check`
  (hookless packs and recipe projects are exempt). Every persisted `packs.toml`
  row must include its `content_hash`; malformed or incomplete rows fail closed
  before a library mutation. Each `recipe.check` row has `name` (pack,
  `PACK/RECIPE` ref, or built-in hook), `type` (`pack`/`recipe`/`hook`),
  `status` (`pass`/`fail`), `path`, and `detail` (the reason for a `fail`);
  any `fail` exits 1. An installed pack whose `pyproject.toml` cannot be
  parsed gets a `fail` row in `validate`, is skipped with a warning by `list`, and
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

## Backups and safety

- Every apply creates one backup bundle by default. `backups list|get|restore
  <id>|prune` manage bundles; `get`/`restore` accept full ids, unambiguous
  prefixes, or `latest`. `restore` and `prune` take `--dry-run`. Restore
  previews and confirms like apply, applies the
  whole bundle as one transaction, and refuses to overwrite files changed after
  the backup unless `--force` is passed. Backups store text content only; mode
  and mtime are not preserved. Bundles are owner-only (dirs `0700`, files
  `0600`) and their metadata is replaced atomically. `prune [--keep N] [--older-than DAYS]` falls
  back to the `recipe.backup_keep`/`recipe.backup_max_age_days` settings and
  prints one row per bundle with `action` `planned`, `deleted` or `failed`
  (`detail`, `error`).
- All recipe-local and target-relative paths must be safe relative paths:
  absolute paths, `..` segments, and symlink traversal are rejected before any
  engine-mediated read or write, again after path-field rendering.
- Installing a pack is installing code (same trust model as `pip install`, no
  sandbox). Evaluate before trusting: the `packs add` summary, `packs get`, `validate`'s
  no-import scan, and the golden test harness. Hook workers get an
  allowlisted environment (`PATH`, `HOME`, locale, temp dirs, `UV_*`/`XDG_*`,
  TLS and proxy settings, `SSH_AUTH_SOCK`/`GIT_SSH_COMMAND`, and `PYTHONPATH`
  set to the pack's `src/` only), as do `uv lock` runs on packs. Tokens such as
  `GITHUB_TOKEN` or untaped's `UNTAPED_*` Jira/AWX/GitHub credentials are not
  in the environment, but `UV_*` (possibly index credentials) is, and hooks
  run as the user with full file access (`~/.netrc`, `config.yml`, git
  credential stores). Hook stdout (even raw fd 1 or a
  subprocess) becomes diagnostics and never corrupts the worker protocol.
- Run `untaped skills install --all` (or `untaped skills install untaped-recipe`)
  to install this packaged skill.
