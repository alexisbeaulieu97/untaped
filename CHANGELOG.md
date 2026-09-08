# Changelog

## 5.0.0

- Removed the orchestration capability, its configuration section, and packaged
  skill. No compatibility command is provided.
- Planning now uses GitHub Issues and Projects; architectural rationale lives
  in plain Markdown decisions in the owning repository.
- The six remaining public capabilities keep their existing command roots.
- Private capabilities require `untaped-private` 1.1.0 with this release.

## 4.0.0

- Completed the unified v4 release boundary: one `untaped` wheel and source
  archive, a public source-provenance manifest, and shared local/published
  executable smoke coverage for all seven built-in capabilities.
- Added a restartable, exact-identity release path that validates the reviewed
  candidate, GitHub draft assets, package-index hashes, published smoke, and
  final GitHub draft publication without overwriting immutable release state.
- Preserved imported standalone source history and stable skill IDs while
  documenting the seven root capability commands.

## Historical imports (Wave 1.5)

- Imported `untaped-workspace` (approved OID `f9c2fd6`) as the built-in
  `untaped workspace` capability with history preserved. Standalone
  packaging (console script, per-tool version, release workflow, lockfile)
  is retired; behavior is preserved per the Wave 1.5 parity matrix.

## Standalone history (`untaped-workspace`, preserved)

## 0.11.0 - 2026-07-03

- Adopted `untaped>=3.0.0,<4` and renamed pipe kinds rejected by SDK 3.0
  validation: `workspace.summary` → `workspace.repo.summary`,
  `workspace.sync-outcome` → `workspace.sync_outcome`,
  `workspace.foreach-outcome` → `workspace.foreach_outcome`, and
  `workspace.branch-outcome` → `workspace.branch_outcome`.
- Changed `remove --prune` and `forget --prune` to use the SDK batch
  preview/confirmation contract. Declining a prompt exits cleanly without
  mutation, and noninteractive prune still requires `--yes` / `-y`.
- Moved workspace registry storage to the SDK `StateCollection` helper; duplicate
  workspace names and paths remain workspace-specific errors, while malformed
  registry collection shape now uses SDK state wording.

## 0.10.0 - 2026-06-28

- Changed `foreach` to close child stdin and apply a 600s default per-repo
  timeout. Timed-out commands return `124` with a `timed out after <Ns>s`
  stderr detail; use `--timeout N` for longer-running commands.
- Changed `sync --all` and `status --all` to keep going when a valid registry
  entry points at a workspace whose manifest is missing, unreadable,
  YAML-invalid, or schema-invalid. These cases now emit workspace-level
  `action="unavailable"` rows with `repo=""` and a detail message.
- Added `action` and `detail` fields to `status` structured output. Normal
  rows use `action="status"`.

## 0.9.0 - 2026-06-28

- Added `target_path` to repo-grain `show --format pipe` records so downstream
  tools can consume the concrete repo checkout path without branching on
  `workspace.repo`. Empty workspace summary rows are tagged
  `workspace.summary` and omit `target_path`.

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
