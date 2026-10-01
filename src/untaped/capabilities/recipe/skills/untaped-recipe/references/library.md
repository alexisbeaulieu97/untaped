# Recipe library: packs, checks, tests and backups

Contents: installing, syncing and removing packs, inspecting before trusting,
what hooks can reach, validation, golden tests, backups.

## Install packs

- `packs add PATH_OR_GIT_URL` installs a pack and lists its recipes and hooks
  on stderr without prompting. `--rev` picks a git revision; `--name` sets
  the installed key, which is the pack's identity everywhere.
- A pack must load, contain a `uv.lock`, and contain no symlinks (ignored
  directories such as `.venv` excepted).
- Reinstalling needs `--force`. It still refuses to overwrite local edits to
  the library copy unless `--discard-edits` is added.
- A local source is recorded as an absolute path; a git source records the
  requested `rev` and the resolved `commit`, shown by `packs list` and in
  `add`/`sync`/`remove` rows.

## Sync and remove packs

- `packs sync PACK...` (or `--all`, or `--stdin` with pack names or
  `recipe.pack` records) re-fetches each pack from its recorded source and
  rev, so a branch or tag moves forward.
- Packs whose content would change are listed with the commit move
  (`old -> new`) and the hook-code files that change (`src/`, root `*.py`,
  `pyproject.toml`, `uv.lock`, `uv.toml`, `.python-version`, `setup.cfg`).
  Show that list to the user; it is the code that will run next.
- Sync confirms (or takes `--yes`); `--dry-run` previews. Rows say
  `updated`, `unchanged` or `planned`.
- A pack with local edits fails unless `--discard-edits` is passed. A failed
  pack gets a `failed` row and the others still sync; the run exits 1, or 5
  when a fetch timed out.
- `packs remove PACK...` confirms, previews with `--dry-run`, and warns when
  the copy has local edits. A `partial` row means removal stopped partway;
  run it again to finish.

## Inspect before trusting

Installing a pack is installing code: the same trust model as
`pip install`, with no sandbox. Before trusting one, read the `packs add`
summary and `packs get`, run `validate` (it never imports hook code), and run
the pack's golden tests.

Each noun reads its own kind: `get`/`edit` for recipes, `packs get`/`packs
edit` for packs (`edit` opens `pyproject.toml`), and `hooks get`/`hooks edit`
for hooks. `hooks list` includes built-ins such as `yaml_edit`, marked
`(builtin)` and not editable.

## What hooks can reach

Hook workers, and the `uv` commands run on a pack (`uv run`, `uv lock`,
`uv lock --check`), get only this environment:

- `PATH`, `HOME`, `USER`/`LOGNAME`, `TZ` and temp directories;
- locale: `LANG`, `LANGUAGE`, `LC_*`;
- `UV_*` (which may hold package-index credentials) and `XDG_*`, `NETRC`;
- TLS trust: `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`,
  `CURL_CA_BUNDLE`;
- proxies: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`;
- for `git+ssh` dependencies: `SSH_AUTH_SOCK`, `GIT_SSH_COMMAND`;
- hook workers only: `PYTHONPATH`, set to the pack's `src/`.

Tokens such as `GITHUB_TOKEN` and untaped's `UNTAPED_*` credentials are not
passed. Hooks still run as the user with full file access, so `~/.netrc`,
`config.yml` and git credential stores are readable. Hooks read an empty
stdin; anything they print becomes diagnostics.

## Validate

- `validate` with no argument checks the whole library and `packs.toml`;
  with a ref or path it checks one pack, recipe or built-in hook.
- It scans hook modules without importing them. A pack that declares hooks
  must have a `uv.lock` that `uv lock --check` accepts.
- Every `packs.toml` row must carry its `content_hash`; a malformed or
  incomplete row blocks every library change until it is fixed.
- Each `recipe.check` row has `name`, `type` (`pack`/`recipe`/`hook`),
  `status` (`pass`/`fail`), `path` and `detail` (why it failed). Any `fail`
  exits 1.
- An installed pack whose `pyproject.toml` does not parse fails `validate`,
  is skipped with a warning by `list`, and is ignored when resolving refs
  unless named explicitly.
- Template and copy sources containing `{{ input }}` tokens are checked only
  up to their literal directory prefix.

## Golden tests

- `test [PACK|PATH|PACK/RECIPE]` runs cases under `tests/<recipe>/<case>/`:
  `given/` is copied to a temporary target, and `expected/` is the full
  expected tree (omit it to assert no change).
- An optional data-only `case.yml` sets `inputs`, `expect: success|error`,
  `error_contains` and `verdict`.
- `--update` regenerates `expected/` for an explicit pack or recipe; review
  the diff before keeping it.
- Any failing case exits non-zero, and so does an explicit ref with no cases.

## Backups

- Every apply writes one backup bundle unless `--no-backup` is passed.
  `backups get` and `restore` accept a full id, an unambiguous prefix, or
  `latest`.
- `restore` previews and confirms like apply and restores the whole bundle as
  one transaction. It refuses to overwrite files changed after the backup
  unless `--force` is passed. With `--format json|yaml|pipe` its row says
  `planned`, `restored` or `failed`.
- Backups hold text content only, not mode or mtime. Bundles are owner-only
  (directories `0700`, files `0600`).
- `backups prune [--keep N] [--older-than DAYS]` falls back to the
  `recipe.backup_keep` and `recipe.backup_max_age_days` settings. Preview
  with `--dry-run`; rows say `planned`, `deleted` or `failed`.
