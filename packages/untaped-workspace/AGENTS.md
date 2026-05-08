# AGENTS.md — `untaped-workspace`

Internals of the workspace bounded context for AI agents and contributors.
For user-facing manifest shape, registry, and command reference, see
[`docs/workspace.md`](../../docs/workspace.md). For workspace-wide rules
(4-layer DDD, Hard Rules, recipes), see the [root
`AGENTS.md`](../../AGENTS.md).

## Manifest + registry split

A workspace has two homes:

- **Manifest** (per-workspace, source of truth): `<workspace-dir>/untaped.yml`
  declares `name`, `defaults` (`branch`), and `repos` (list of `{url, name?,
  branch?}`). Read/written by `infrastructure.ManifestRepository`.
- **Registry** (central): a `name → path` list under `workspace.workspaces`
  in `~/.untaped/config.yml`. Just enough to power `list`, `path <name>`,
  `--name X` lookups, and tab completion. Read/written by
  `infrastructure.WorkspaceRegistryRepository`.

Method names on the registry are `entries`, `get`, `find_by_path`,
`register`, `unregister` — *not* `list`, which would shadow the `list`
builtin in nested annotations within the class.

## Workspace lookup precedence

Every command except `list` / `path` / `shell-init` / `edit`:

1. Explicit `--name` → registry lookup
2. Explicit `--path` → manifest lookup
3. Otherwise: walk up from cwd looking for `untaped.yml`

Implemented in `infrastructure.WorkspaceResolver`.

## Git is a subprocess, not a library

`infrastructure.GitRunner` is the **only** place `subprocess` is imported
inside this package. Domain and application layers depend on its
`Protocol`, so tests can stub it. Bare clones are cached in
`workspace.cache_dir` (default `~/.untaped/repositories`); workspace
clones use `git clone --reference` against the bare so disk + bandwidth
are shared without `git worktree` branch conflicts.

## `system_adapters` for other side effects

Other side-effecting calls (shell-out for `foreach`, editor launch for
`edit`, `rmtree` for `remove --prune` / `sync --prune`) live behind
`infrastructure.system_adapters` as three small adapter types:

- `ShellRunner` — `Callable` alias (one operation)
- `EditorRunner` — `Callable` alias (one operation)
- `Filesystem` — `Protocol` (groups `rmtree` and any future side-effecting
  fs operation)

Application use cases require those adapters as constructor arguments —
**none of them imports `subprocess` or `shutil` directly.** The CLI
composition root wires the defaults; tests inject stubs.

## `foreach` output semantics

Honours the standard piping contract:

- `--format table` (default): replays each repo's captured stdout / stderr
  with a `[<repo>] line` prefix once that repo's command finishes (output
  is buffered per repo by the underlying runner — chatty commands won't
  appear until they exit).
- `--format json|yaml|raw`: emits one `ForeachOutcome` row per repo
  (including `command` and `duration_s`) after every repo finishes.

## Branch cascade is clone-time only

Per-repo `branch` > workspace `defaults.branch` > the remote's HEAD —
honoured **only at clone time**. Subsequent `sync`s do *not* auto-switch
branches: they skip-with-warning when the on-disk branch doesn't match the
manifest's target. This stops a stale `defaults.branch` from kidnapping a
user mid-`feature/x`. State machine: `application.SyncWorkspace._sync_repo`.

## `sync --only` strict vs. relaxed semantics

`SyncWorkspace.__call__` takes `strict_only: bool = True`. The CLI passes
`strict_only=not all_workspaces`, so:

- **Single-workspace mode** (`sync --name x --only typo`): strict. The
  use case raises `UnmatchedOnlyFilter(WorkspaceError)`, a typed
  exception carrying `unmatched: tuple[str, ...]`. Callers can `except`
  precisely without parsing the error message.
- **`--all` mode** (`sync --all --only repo-x`): relaxed. Workspaces
  whose manifests don't contain `repo-x` emit one
  `SyncOutcome(action="unmatched", repo=<identifier>, detail="not in
  this workspace's manifest")` row per requested identifier and
  continue. The discriminator lives in the type-safe `action` Literal
  (extended with `"unmatched"`) so `awk` / `cut` / `jq` consumers can
  pattern-match cleanly without overloading the `repo` data column with
  a sentinel like `<all>`.

Partial-miss is also visible: `--only repo-x,typo` against
`[repo-x, repo-y]` emits a sync row for `repo-x` AND an `unmatched`
row for `typo` — the typo doesn't get silently swallowed because a
sibling identifier matched.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules, recipes
- [`docs/workspace.md`](../../docs/workspace.md) — user-facing manifest,
  registry, command reference, `uwcd` shell helper
