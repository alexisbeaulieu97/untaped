# Changelog

## 0.7.0 - 2026-06-22

- Changed `sync --parallel` / `sync -j` to mean concurrent repo sync jobs for
  both single-workspace sync and `sync --all`. Previously, `sync --all -j`
  capped concurrent workspaces.
- Added repo-oriented sync progress and a stderr summary while keeping
  `SyncOutcome` output rows unchanged for `json`, `yaml`, `raw`, and `pipe`
  formats.
- Avoided redundant bare-cache fetches after a fresh bare clone and kept
  existing local clones from touching the bare cache during sync.
