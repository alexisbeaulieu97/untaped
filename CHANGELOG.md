# Changelog

## Unreleased

Correctness and safety fixes from a whole-codebase review. Items marked
**behavior change** alter output, exit codes, or defaults.

- Core
  - `config set` validates the raw value against the setting's type instead of
    parsing it as YAML: `#` no longer truncates secrets, and numeric strings
    such as `0123456` are accepted for string settings. **Behavior change:**
    for string settings `null` is now stored literally; it still clears
    optional non-string settings. Use `config unset KEY` to clear a setting.
  - `config` and `profile` commands validate only the section they touch, so a
    broken section can be repaired from the CLI (including with
    `config set KEY --prompt`). Non-mapping config shapes are
    reported as config errors instead of tracebacks.
  - `doctor` reports one row per core, capability, and state section,
    including `UNTAPED_*` overrides and the selected profile.
  - **Behavior change:** `config list/get` and `profile list` emit native
    values (`null`, booleans, numbers) in json/yaml/pipe; glyphs remain in
    tables only. A closed output pipe exits 0. `--verbose` with `--quiet` is a
    usage error. `--columns` accepts comma lists and rejects unknown columns
    for typed records.
  - `profile current` honors `--profile`; a missing, unreadable, or invalid
    `http.ca_bundle`, an unknown `ui.theme`, and cross-origin pagination links
    are reported clearly. Config writes create the temporary file with mode
    0600.
- workspace
  - Repo and workspace names must be a single safe path segment; the bare
    cache path is sanitized.
  - **Behavior change:** `forget --prune` deletes only managed clones and the
    manifest, and leaves other files in place. `sync --prune` now confirms
    (`--yes` to skip). `sync` and `branch apply` report failures as `failed`
    and exit 1. `branch apply` no longer creates missing branches unless
    `--create` is passed.
  - Git never runs in an enclosing repository, never prompts for
    credentials, and fast-forwards from the branch's upstream. The bare cache
    now actually refreshes; new clones copy objects out of it
    (`--dissociate`) and it is never auto-gc'd, so pruned cache branches
    cannot corrupt clones. Ctrl-C stops queued work and every running child
    process, including under `foreach --parallel`; `foreach` streams rows as
    repos finish.
- github
  - `sweep --grep` always uses extended regular expressions, so `a|b` works
    and git config cannot change results.
  - `search code`/`search issues` batch repo scopes within GitHub's operator
    limit instead of truncating or failing.
  - CODEOWNERS matching follows GitHub's gitignore-style rules.
  - One bad repo no longer aborts a sweep; failed refreshes that fall back to
    cached copies are reported. `cache clean --all --org X` only removes X.
    Branches and tags with the same name are both scanned.
- jira
  - `issue assigned --jql` keeps the configured assignee filter, and bare
    `issue search` uses `jira.assigned_jql`. JQL starting with `ORDER BY` and
    sprint functions render correctly. `issue get` shows description, type,
    priority, reporter, labels, created, and resolution.
- awx
  - `--extra-vars` is sent as a mapping (`KEY=VAL`, `@file`, or JSON/YAML).
  - **Behavior change:** launch checks the template's ask-on-launch flags
    first and fails rows whose fields AWX ignored. Launching or syncing more
    than one target asks for confirmation (`--yes` to skip). `patch` rejects
    unknown fields (`--allow-unknown-fields` to opt out). `jobs list`
    defaults to 20 rows (`--limit 0` for all).
  - Replacing a credential with one of the same type works. `patch --set`
    keeps string fields as strings. Ctrl-C interrupts waits and prints the
    running job IDs. `ping` validates the token. Fewer API requests for
    scoped selection, `list --limit`, and `delete`.
- recipe
  - Writes preserve file permissions. Glob steps work through symlinked
    targets. One broken installed pack no longer breaks every command.
    `yaml_edit` leaves files untouched when nothing changes.
  - **Behavior change:** `hook run` no longer runs hooks from the current
    directory implicitly (use `--project`), and bare hook names inside a pack
    resolve to that pack's hooks and built-ins only (use `pack/hook` across
    packs). The hook worker runs isolated from untaped's own modules.
- ansible
  - `graph ./repo` resolves the origin through git (worktrees, duplicate
    config keys) and git output parsing no longer depends on the locale.
  - Shared dependencies are expanded once, fixing exponential time and
    output on large graphs.
  - Versions such as `1.10` stay strings, repo names match
    case-insensitively, GitHub Enterprise URLs resolve, and unpinned
    dependents are included for the default branch. The first refresh after
    upgrading rescans each source.
- Tests run hermetically: local runs no longer read the developer's config or
  depend on terminal width, and coverage is gated in CI at 89%.

## 6.0.1

- `github sweep` now retries transient Git transport failures (dropped TLS/TCP
  connections, `early EOF`, HTTP 429/5xx) with short backoff instead of
  reporting the repository as unscanned.
- Wide sweeps (`--refs branches|tags|all`, `--ref GLOB`) list remote refs first
  and fetch only new or moved refs in bounded batches, so interrupted refreshes
  resume where they stopped and unchanged repositories skip fetching.

## 6.0.0

- Added consistent AWX bulk patch and external-editor workflows across eight
  writable object types, with complete preflight, conflict checks, one batch
  confirmation, and per-object outcomes.
- Added inventory and inventory-source management, including constructed
  inventories and explicit inventory refresh through `sync`.
- Standardized project and inventory refresh, execution tracking, and failure
  reporting.
- Breaking changes: use `patch` or `edit` instead of `apply --stdin`; use
  `--continue-on-error` instead of `--fail-fast`; use `projects sync` instead
  of `projects update`. `apply FILE` remains declarative.
- Updated AWX user guidance and packaged skills. Private capabilities require
  `untaped-private` 1.2.0 with this release.

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
