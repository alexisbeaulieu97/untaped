# Changelog

## 0.8.0 - 2026-06-27

- Changed prune safety so `sync --prune` no longer deletes clean orphan
  clones with commits or local tags not reachable from local
  remote-tracking refs. Unsafe orphans are skipped with `unsafe local
  state: ...`; corrupt/uninspectable or symlinked orphans are also
  skipped. `sync --prune` remains prompt-free and has no `--yes`.
- Changed `remove --prune` and `forget --prune` to refuse clones with
  stash entries or commits/local tags not reachable from local
  remote-tracking refs, not just dirty worktrees, before mutating
  manifests, registry state, or files.
- Fixed `forget --prune` to inspect immediate child git clones that are
  not declared in the manifest before deleting the workspace directory,
  while skipping symlinked child entries because workspace deletion only
  unlinks them.

## 0.7.0 - 2026-06-22

- Changed `sync --parallel` / `sync -j` to mean concurrent repo sync jobs for
  both single-workspace sync and `sync --all`. Previously, `sync --all -j`
  capped concurrent workspaces.
- Added repo-oriented sync progress and a stderr summary while keeping
  `SyncOutcome` output rows unchanged for `json`, `yaml`, `raw`, and `pipe`
  formats.
- Avoided redundant bare-cache fetches after a fresh bare clone and kept
  existing local clones from touching the bare cache during sync.
