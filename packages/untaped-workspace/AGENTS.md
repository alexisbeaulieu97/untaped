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

**Manifest mutation contract.** All three manifest models (`Repo`,
`ManifestDefaults`, `WorkspaceManifest`) are `frozen=True`. Mutation
goes through `WorkspaceManifest.add_repo(repo) -> WorkspaceManifest`
and `WorkspaceManifest.remove_repo(ident) -> tuple[WorkspaceManifest,
Repo]`, which return new manifests rather than mutating in place.
Every manifest construction in the application layer uses
`WorkspaceManifest(...)` — *not* `model_copy(update=...)` — because
pydantic v2's `model_copy` deliberately skips validators. Using direct
construction uniformly keeps the
`@model_validator(mode="after")` duplicate-rejection check available
on every mutation, including non-repo-list edits like the rename in
`ImportWorkspace`. `add_repo` raises typed `DuplicateRepoUrl` /
`DuplicateRepoName` exceptions (subclasses of `ValueError`), each
carrying the incumbent `Repo` so callers can format CLI-facing
errors without re-scanning the manifest; `remove_repo` raises
`ValueError` for an unknown ident. Application use cases catch the
typed exceptions and translate to `WorkspaceError` with the
disambiguation hints. No application-layer code mutates
`manifest.repos` directly.

## Workspace lookup precedence

Lookup-precedence applies only to commands that act on an existing
workspace by name or path — `add`, `remove`, `sync`, `status`,
`foreach`. For those commands:

1. Explicit `--name` → registry lookup
2. Explicit `--path` → manifest lookup
3. Otherwise: walk up from cwd looking for `untaped.yml`

Implemented in `application.WorkspaceResolver` — takes a `RegistryReader`
+ `ManifestReader` via constructor injection, so precedence can be
unit-tested with stubs (no real registry or on-disk manifest required).
The CLI composition root wires `WorkspaceRegistryRepository` and
`ManifestRepository`.

Lifecycle and single-target commands (`init <name>`, `adopt <path>`,
`forget <name>`, `import <source>`, `path <name>`, `edit <name>`) take
positional arguments and skip the precedence walk. `list` and
`shell-init` operate without a workspace target.

## Git is a subprocess, not a library

`subprocess` is imported in only two places in this package, both
inside `infrastructure/`: `GitRunner` (this section) and
`system_adapters` (covered next, for shell-out / editor-launch /
`rmtree`). Domain and application layers depend on `GitRunner`'s
`Protocol`, so tests can stub it. Bare clones are cached in
`workspace.cache_dir` (default `~/.untaped/repositories`); workspace
clones use `git clone --reference` against the bare so disk + bandwidth
are shared without `git worktree` branch conflicts.

Every `subprocess.run` inside `GitRunner` carries a `timeout=` budget
so a hung remote never strands a `sync --all` sweep. The split is
**local-only vs network**, not "read-only vs write" (the local-only
bucket includes `ff_only_pull`, which does write HEAD via
`git merge --ff-only` — but executes locally with no network round
trip, so the fast budget still applies). Local-only methods
(`status`, `is_dirty`, `default_branch`, `read_remote_url`,
`read_current_branch`, `ff_only_pull`) get `DEFAULT_TIMEOUT`; network
methods (`ensure_bare` clone, `bare_fetch`, `clone_with_reference`,
`fetch`) get `DEFAULT_SLOW_TIMEOUT`. Override per-instance via
`GitRunner(timeout=…, slow_timeout=…)`. The CLI surfaces
`workspace sync --timeout N`, which sets both buckets to `N` for that
invocation (so a CI script can fail fast on hung clones with a single
knob). Timeouts surface as `GitError("git <args> timed out after Ns")`,
which `SyncWorkspace._sync_repo`'s existing `GitError` handlers
translate to `skip` rows without further plumbing.

## Ports module

Every cross-use-case `Protocol` and Callable alias lives in
`application/ports.py`. Use cases declare the narrowest port they
need (`ManifestReader` vs. `ManifestRepository`, `StatusInspector`
vs. `GitInspector` vs. `GitOperations`); fatter ports extend slimmer
ones via `Protocol` inheritance, so the concrete
`ManifestRepository` / `WorkspaceRegistryRepository` / `GitRunner` /
`LocalFilesystem` / `LocalRepoDiscoverer` adapters satisfy every
variant structurally with no explicit base class.

Mirrors `untaped-awx`'s `application/ports.py`. When you add a new
shared port, put it here — never declare a private `_FooStorage`
inside a use-case file.

DTOs that cross the application/infrastructure boundary
(`DiscoveredRepo`, `DiscoveryResult`, `ManifestSource`) live in
`domain/payloads.py` as pydantic `BaseModel`s — keeps the
`infrastructure → domain` arrow clean and matches the rest of the
package's pydantic-everywhere convention.

## `system_adapters` for other side effects

Other side-effecting calls (shell-out for `foreach`, editor launch for
`edit`, plus every disk read/write — `exists`, `is_dir`, `mkdir`,
`iterdir`, `rmtree`) have their default implementations in
`infrastructure.system_adapters`:

- `shell_runner` — concrete factory satisfying `application.ports.ShellRunner`
- `editor_runner` — concrete factory satisfying `application.ports.EditorRunner`
- `resolve_editor_argv(editor, *, env=None, posix=None) -> tuple[str, ...]`
  — resolves the editor selection (`--editor` / `$VISUAL` / `$EDITOR` /
  `"vi"`) and `shlex`-splits it. Lives here, not in `application/`,
  because `os.environ` / `os.name` are the same kind of process-state
  side effect as `subprocess`. `EditWorkspace` receives a fully-resolved
  `argv` from the CLI composition root and never imports `os` or
  `shlex`. The `env` / `posix` knobs are for unit tests so both
  splitting branches are exercised without a Windows CI runner. Raises
  `WorkspaceError` on an empty argv and on `shlex.split` failures.
- `LocalFilesystem` — concrete class satisfying `application.ports.Filesystem`,
  which declares `exists` / `is_dir` / `mkdir(*, parents, exist_ok)`
  (no defaults — call sites pass both kwargs explicitly so the
  divergence from `pathlib.Path.mkdir`'s `False/False` can't slip
  through silently) / `iterdir` / `rmtree`. Methods delegate to the
  equivalent `pathlib.Path` operation (or `shutil.rmtree` for the
  recursive delete).

Application use cases require the port shapes as constructor arguments
— **none of them imports `subprocess` or `shutil`, or reaches into
`pathlib` for filesystem reads/writes directly.** Every disk touch
flows through the `Filesystem` port; the contract is pinned by
`tests/unit/test_filesystem_port.py::test_no_pathlib_io_in_application_layer`,
which greps `application/` for `.is_dir()` / `.exists()` / `.iterdir()`
/ `.mkdir()` and fails CI on any leak. The CLI composition root wires
the defaults; tests inject the conftest `StubFilesystem` to assert
disk side effects without `tmp_path`.

**`self._<name>` convention.** Use cases must hold their port on a
private attribute (`self._fs`, `self._manifests`, `self._registry`, …)
and call it via that attribute. The lint test's port-call exception
matches only the `self\._\w+\.method(` shape — a locally-bound
`fs = self._fs` followed by `fs.is_dir(p)` would trip a false-positive
leak report. The convention also keeps the seam visually obvious at
the call site.

**Two small caveats** the rule does **not** ban:

- `Path.resolve()` / `Path.expanduser()` for input-normalisation
  (`init_workspace.py`, `import_workspace.py`, `adopt_workspace.py`).
  `resolve()` *does* hit syscalls to walk symlinks, but it's
  path-normalisation rather than data-touching I/O and there's no
  test-stub value in routing it through the port today.
- `ManifestReader.exists(workspace_dir)` (`ports.py`) — also reads the
  disk, but it's a port (asks "is there a manifest under this dir?")
  with different semantics from `Filesystem.exists`; the lint
  whitelists port-mediated calls regardless of the receiver name.

## `foreach` output semantics

Honours the standard piping contract:

- `--format table` (default): replays each repo's captured stdout / stderr
  with a `[<repo>] line` prefix once that repo's command finishes (output
  is buffered per repo by the underlying runner — chatty commands won't
  appear until they exit).
- `--format json|yaml|raw`: emits one `ForeachOutcome` row per repo
  (including `command` and `duration_s`) after every repo finishes.

Error handling has three modes: default fail-fast (break on first
non-zero exit), `--continue-on-error` (walk every repo, exit 1 if any
failed), and `--ignore-errors` (walk every repo, **exit 0** — usable
inside `set -e` scripts). On `--format table`, a `failed in: a, b, c`
summary is appended to stderr whenever at least one repo failed,
regardless of mode — failures aren't silent even when ignored. The
summary is suppressed in `--format json|yaml|raw` since each row's
`returncode` already conveys the same information.

## Branch cascade is clone-time only

Per-repo `branch` > workspace `defaults.branch` > the remote's HEAD —
honoured **only at clone time**. Subsequent `sync`s do *not* auto-switch
branches: they skip-with-warning when the on-disk branch doesn't match the
manifest's target. This stops a stale `defaults.branch` from kidnapping a
user mid-`feature/x`. State machine: `application.SyncWorkspace._sync_repo`.

## `sync --all -j N` parallelism

`workspace sync --all -j N` dispatches workspaces across a
`ThreadPoolExecutor` in `cli/commands.py::_sync_parallel`. Outcomes
come back in `as_completed` order then get re-sorted by
`(workspace_input_order, repo)` so JSON/table consumers see stable
rows regardless of completion timing. The CLI clamps `-j` at
`_PARALLEL_CAP=32` and rejects `parallel > 1` outside `--all` with a
`typer.BadParameter` (single-workspace parallelism would have to push
inside `SyncWorkspace` and racing the bare-cache per-repo isn't worth
the complexity — see #30). When `--all -j N>1` is in play, the
CLI emits a stderr "syncing N workspaces with up to M workers" header
so users can tell the parallel path was taken.

Bare-fetch dedup lives on a session-scoped `BareFetchTracker` object
(`fetched: set[Path]` + per-URL `threading.Lock`s + a guard) **owned
by the composition root**. The CLI's `sync` command allocates one per
invocation and threads it into every `SyncWorkspace.__call__` —
serial path and `_sync_parallel` alike — so workspaces sharing a
repo URL share one `ensure_bare + bare_fetch` round-trip. The
`BareFetchTracker.lock_for(url)` helper serialises
`ensure_bare → check → fetch → add` for one URL while letting threads
on different URLs proceed in parallel. On `GitError`, the URL is
left out of `tracker.fetched` so the next caller retries — same
semantics as the pre-parallel serial path. `__call__` accepts
`bare_tracker: BareFetchTracker | None = None`; the default allocates
a fresh per-call tracker (no cross-call dedup), which is what
single-workspace callers and unit tests want. **Never reach into
`tracker.fetched` directly from a worker — always go through the use
case's `_ensure_bare_fresh` so the per-URL lock is honoured.**

## `init` vs. `adopt` vs. `import` vs. `forget`

The shared opening for every bootstrap-style entry point
(`init` / `adopt` / `import`) — canonicalise the target path,
derive `ws_name`, raise on manifest/registry collision, `mkdir`,
write the manifest, register — lives on
`application.WorkspaceBootstrapper`. Each lifecycle use case
constructs the bootstrapper with the same
`ManifestRepository` / `WorkspaceRegistryRepository` / `Filesystem`
adapters at the CLI composition root, then delegates by passing a
`build_manifest(ws_name) -> WorkspaceManifest` closure that owns the
caller-specific variation (e.g. `init` plugs in the `--branch` flag;
`adopt` plugs in the discovered repos; `import` plugs in the
external-manifest contents). `ForgetWorkspace` is *teardown* and does
not flow through the bootstrapper.

Four workspace lifecycle commands with deliberately distinct shapes:

- **`workspace init <name>`** — empty workspace by name; defaults the
  on-disk location to `<workspace.workspaces_dir>/<name>` (the
  `workspaces_dir` setting is profile-overridable, default
  `~/.untaped/workspaces`). Pass `-p / --path` to override the location
  for a one-off workspace that lives elsewhere. Implementation:
  `application.InitWorkspace`.
- **`workspace adopt <path>`** — initialises a workspace from
  already-cloned repos. Each immediate subdirectory containing `.git` is
  recorded in the new manifest with its current `origin` URL and
  checked-out branch (`infrastructure.LocalRepoDiscoverer` walks the
  directory and reads both via `GitRunner.read_remote_url` /
  `read_current_branch`; a detached HEAD becomes `branch: null`;
  missing-`origin` clones surface via a stderr `warn` hook). Adopted
  clones do not share objects with the bare cache — the cascade only
  links new clones via `git clone --reference`. `<path>` stays
  positional because adopt fundamentally targets an already-populated
  directory. Implementation: `application.AdoptWorkspace`.
- **`workspace import <source> --path <dest>`** — bootstraps a workspace
  from a local YAML manifest file (e.g. one cloned from a shared repo).
  Reads the external manifest via `ManifestRepository.read_external`
  (the narrow `ExternalManifestReader` port is what `ImportWorkspace`
  declares as its direct dep), then re-emits a manifest at `dest` whose
  `name` falls back through `--name → loaded.manifest.name →
  canonical.name`. `--sync` chains a `SyncWorkspace` call to clone the
  declared repos after registration. Implementation:
  `application.ImportWorkspace`.
- **`workspace forget <name>`** — removes the workspace's registry
  entry only; the on-disk manifest and clones are preserved. Pass
  `--prune` to also `rmtree` the workspace directory; pruning is
  refused (mirroring `workspace remove --prune`) when any declared repo
  has uncommitted changes. A missing manifest or missing directory are
  tolerated — the registry entry is removed regardless. Implementation:
  `application.ForgetWorkspace` with explicit `Filesystem` +
  dirty-checker ports, wired to `LocalFilesystem` + `GitRunner` at the
  CLI composition root.

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

## Shared test stubs

`StubGit` (satisfies the `GitRunner` port), `StubRegistry`
(satisfies `WorkspaceRegistryRepository`), `StubFilesystem`
(satisfies the `Filesystem` port with an in-memory set of paths so
use-case tests assert disk predicates without touching `tmp_path`),
`StubManifests` (satisfies the `ManifestRepository` port with an
in-memory map — used by the resolver's stub-driven unit tests where
the real adapter would force a real workspace on disk), and the
`empty_manifest()` helper (default-constructed
`WorkspaceManifest` for tests that only need an empty file on disk)
live in `tests/conftest.py`. Sibling unit tests import them via
`from conftest import StubGit, StubRegistry, StubFilesystem,
StubManifests, empty_manifest` — the root
`pyproject.toml` adds this package's `tests/` dir to
`[tool.pytest.ini_options] pythonpath` so the conftest module is
runtime-importable (pytest's `--import-mode=importlib` otherwise
hides it). See the conftest docstring for the global-namespace
caveat. New shared scaffolding for this package belongs in the
same module; new shared scaffolding for *other* packages must pick
a unique module name (e.g. `_<pkg>_stubs.py`) — `conftest` is
claimed.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules, recipes
- [`docs/workspace.md`](../../docs/workspace.md) — user-facing manifest,
  registry, command reference, `uwcd` shell helper
