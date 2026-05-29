# AGENTS.md - `untaped-workspace`

Single source of truth for this standalone plugin repo. If you change
architecture, command behavior, settings behavior, or the development
workflow, update this file in the same commit.

## Mission

`untaped-workspace` is an `untaped` plugin. It owns the `untaped workspace`
command group for local git workspaces: per-workspace `untaped.yml`
manifests, central registry state, git sync/status operations, and shell
helpers. `untaped` core owns the binary, plugin discovery, config/profile
resolution, output helpers, stdin helpers, HTTP/TLS primitives, and shared
errors.

## Hard Rules

1. **Keep `AGENTS.md` up to date.** Architecture changes and new command
   patterns must be documented here.
2. **Prefer `uv` commands over manual dependency edits.** Use `uv add` and
   `uv add --group dev`; hand-edit tool config only.
3. **Expose the plugin through the `untaped.plugins` entry point.**
   `workspace = "untaped_workspace.plugin:plugin"` is the public integration
   point.
4. **Use the 4-layer DDD layout.** `cli -> application -> domain`, with
   `infrastructure -> domain`; `application` and `infrastructure` must not
   import each other at runtime.
5. **Declare ports in `application/ports.py`.** Use cases depend on the
   narrowest `Protocol`; concrete adapters satisfy ports structurally.
6. **Use absolute imports.** `from untaped_workspace...` and
   `from untaped...`, never relative imports.
7. **Every source module has a module docstring.** Re-export `__init__.py`
   files are exempt.
8. **Every Typer app and every command with required args sets
   `no_args_is_help=True`.**
9. **stdout is data only.** Prompts, progress, and status messages go to
   stderr via `typer.echo(..., err=True)`.
10. **Pipe-friendly commands keep stable raw identifiers.** Workspace
    registry rows start with `name`; sync/status/foreach rows start with
    `workspace`.
11. **Plugin code reads typed settings through `get_config_section`.** Use
    `get_config_section("workspace", WorkspaceSettings)`, not a global
    aggregate `settings.workspace` attribute.
    Commands that read registry or profile settings expose the core
    command-local `ProfileOverrideOption` as `--profile` and wrap the command
    body in `profile_override(profile)`.
12. **All git subprocess calls live behind infrastructure ports.** New git
    operations go in `GitRunner`; application code depends on Protocols.
13. **Finish with verification.** Run `uv run ruff check --fix`,
    `uv run ruff format`, `uv run mypy`, and `uv run pytest`.

## Architecture

```text
src/untaped_workspace/
├── __init__.py           # re-exports app
├── plugin.py             # entry-point plugin object
├── settings.py           # plugin-owned profile settings and app state
├── cli/                  # Typer commands; composition root
├── application/          # use cases and ports
├── domain/               # pure models and value objects
└── infrastructure/       # git, manifest, registry, filesystem adapters
```

The plugin object registers `WorkspaceSettings` as the `workspace` profile
settings section, `WorkspaceState` as the top-level `workspace` app-state
section, and mounts the Typer app as the root `workspace` command.

## Manifest + registry split

A workspace has two homes:

- **Manifest** (per-workspace, source of truth): `<workspace-dir>/untaped.yml`
  declares `name`, `defaults` (`branch`), and `repos` (list of `{url, name?,
  branch?}`). Read/written by `infrastructure.ManifestRepository`.
- **Registry** (central): a `name → path` list under `workspace.workspaces`
  in `~/.untaped/config.yml`. Just enough to power `list`, `path <name>`,
  `--workspace X` lookups, and tab completion. Read/written by
  `infrastructure.WorkspaceRegistryRepository`.

`ManifestRepository.write` owns workspace-dir creation — it mkdirs the
manifest's parent (which *is* the workspace dir) before writing. Use
cases do not call `Filesystem.mkdir` / `Path.mkdir` for the workspace
root themselves; they persist the manifest and the directory exists as
a side effect.

Method names on the registry are `entries`, `get`, `find_by_path`,
`register`, `unregister` — *not* `list`, which would shadow the `list`
builtin in nested annotations within the class.

**Manifest mutation contract.** All three manifest models (`Repo`,
`ManifestDefaults`, `WorkspaceManifest`) are `frozen=True`, and
`WorkspaceManifest.repos` is typed `tuple[Repo, ...]` (not `list`) so
in-place mutation — `m.repos.append(r)`, `m.repos[0] = r` — raises at
runtime. Pydantic's `frozen=True` only blocks attribute reassignment,
not container mutation; the tuple closes that hole structurally. All
edits go through `WorkspaceManifest.add_repo(repo) -> WorkspaceManifest`,
`WorkspaceManifest.remove_repo(ident) -> tuple[WorkspaceManifest, Repo]`,
`WorkspaceManifest.with_default_branch(branch) -> WorkspaceManifest`,
and `WorkspaceManifest.with_repo_branch(ident, branch) ->
tuple[WorkspaceManifest, Repo]`, which return new manifests rather than
mutating in place.
Every manifest construction in the application layer uses
`WorkspaceManifest(...)` — *not* `model_copy(update=...)` — because
pydantic v2's `model_copy` deliberately skips validators **and field
coercion**. Direct construction uniformly keeps the
`@model_validator(mode="after")` duplicate-rejection check available
on every mutation, including non-repo-list edits like the rename in
`ImportWorkspace`. `model_copy(update={"repos": [...]})` would *also*
leave `.repos` as a plain `list` after the copy, defeating the
tuple-based structural freeze; another reason to stay on the
direct-construction path. `add_repo` raises typed `DuplicateRepoUrl` /
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
`foreach`, `show`, `branch set`, `branch unset`, and `branch apply`.
For those commands:

1. Explicit `--workspace` / `-w` → registry lookup
2. Explicit `--path` → manifest lookup
3. Otherwise: walk up from cwd looking for `untaped.yml`

Implemented in `application.WorkspaceResolver` — takes a `RegistryReader`
+ `ManifestReader` via constructor injection, so precedence can be
unit-tested with stubs (no real registry or on-disk manifest required).
The CLI composition root wires `WorkspaceRegistryRepository` and
`ManifestRepository`.

All workspace commands that read registry state or workspace profile
settings expose command-local `--profile <name>` and use
`profile_override(profile)` around those reads. That includes lifecycle
commands such as `init`, `adopt`, `import`, `forget`, `path`, and `edit`
because they read/write the central registry or consume
`workspace.workspaces_dir` / `workspace.cache_dir`. `shell-init` is the
only current command that intentionally stays profile-neutral.

Lifecycle and single-target commands (`init <name>`, `adopt <path>`,
`forget <name>`, `import <source> <dest>`, `path <name>`, `edit <name>`)
take positional arguments and skip the precedence walk. `list` and
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

`GitRunner.fetch` explicitly fetches all `origin` branch heads into
`refs/remotes/origin/*`, instead of relying on the clone's configured
fetch refspec. Some existing/adopted repos are single-branch clones whose
refspec only tracks the original branch; widening the fetch here lets
`workspace branch apply` discover later manifest target branches when
they already exist on the remote.

## Ports module

Every cross-use-case `Protocol` and Callable alias lives in
`application/ports.py`. Use cases declare the narrowest port they need;
this package's concrete chains are
`ManifestReader ⊂ ManifestRepository`, `RegistryReader ⊂
WorkspaceRegistry`, and `StatusInspector ⊂ GitInspector ⊂
BranchOperations ⊂ GitOperations`. The concrete `ManifestRepository` /
`WorkspaceRegistryRepository` / `GitRunner` / `LocalFilesystem` /
`LocalRepoDiscoverer` adapters satisfy every variant structurally
with no explicit base class. When you add a new shared port, put it
here — never declare a private `_FooStorage` inside a use-case file.

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

`--parallel` (`-j`) shares `untaped.clamp_parallel` with `sync
--all` and `awx apply`: caps at `2 * os.cpu_count()` and emits a stderr
`warning: --parallel N clamped to M (2 * os.cpu_count()).` when the
user asks for more. Foreach silently coerces `<= 0` to serial (issue
spec) — `sync` and `awx apply` raise `BadParameter` for the same input.
Friendly clamp at the upper bound rather than `BadParameter` so `-j
$(nproc)` keeps composing on hosts where `nproc` already exceeds the
cap.

## Branch cascade is clone-time only

Per-repo `branch` > workspace `defaults.branch` > the remote's HEAD.
`SyncWorkspace` honours the cascade for clone-time branch selection,
but subsequent syncs do *not* auto-switch branches: they skip-with-warning
when the on-disk branch doesn't match the manifest's target. This stops a
stale `defaults.branch` from kidnapping a user mid-`feature/x`. State
machine: `application.SyncWorkspace._sync_repo`.

`workspace branch set` and `workspace branch unset` are manifest-editing
commands only: they update `defaults.branch` or a repo override in
`untaped.yml`, then stop unless `branch set --apply` is passed.
`workspace branch apply` is the explicit checkout command for existing
clones. It fetches, reads status, refuses dirty/diverged repos, and calls
`GitRunner.checkout_branch` only when a clean clone is on a different
branch from the manifest target. `GitRunner.checkout_branch` checks out an
existing local branch when present; if the local branch is missing but
`origin/<branch>` exists, it creates a local tracking branch from that
remote ref and sets the upstream config explicitly so narrow single-branch
clones work too. If neither local nor `origin/<branch>` exists, it creates
a local branch from the current clean HEAD. Missing clones, repos without
a target branch, fetch failures, status failures, and checkout failures are
row-level `skip`s. `workspace show` is manifest-only; it formats the
effective branch cascade without reading live git state.

## `sync --all -j N` parallelism

`workspace sync --all -j N` dispatches workspaces through the
plural `application.SyncWorkspaces` use case. The plural wraps the
singular `SyncWorkspace` (constructor dep, typed via the
`SyncWorkspaceCallable` Protocol in `application/ports.py`) with
serial/parallel dispatch, outcome sort, and a `notify` stderr-hook
seam. The CLI builds the singular, wraps it, and invokes the plural
via `SyncWorkspaces.__call__(targets, parallel=workers, ...)` — no
executor lives in `cli/commands.py` any more. The singular is still
the right primitive for `add --sync` / `import --sync`, which sync a
single workspace and don't need the plural's dispatch.

When `parallel > 1` *and* `len(workspaces) > 1`, the plural emits
`"syncing N workspaces with up to M workers"` via `notify`
(`cli/commands.py` wires `notify=lambda m: typer.echo(m, err=True)`),
dispatches via `ThreadPoolExecutor`, drains via `as_completed`, and
re-sorts outcomes by `(workspace_input_order, repo)` so JSON/table
consumers see stable rows regardless of completion timing. Below that
threshold the plural runs a serial loop and stays silent (no header,
no pool). The CLI clamps `-j` through `untaped.clamp_parallel`
(cap = `2 * os.cpu_count()`, shared with `foreach` and `awx apply`)
and rejects `parallel > 1` outside `--all` with `typer.BadParameter` —
single-workspace parallelism would have to push inside `SyncWorkspace`
and racing the bare-cache per-repo isn't worth the complexity.

Bare-fetch dedup lives on a session-scoped `BareFetchTracker` object
(`fetched: set[Path]` + per-URL `threading.Lock`s + a guard).
`SyncWorkspaces` allocates one per call by default and threads it
into every `SyncWorkspace.__call__` in the sweep — serial and parallel
paths alike — so workspaces sharing a repo URL share one
`ensure_bare + bare_fetch` round-trip. Callers that need cross-call
dedup can pass an explicit `bare_tracker=` (kept on the plural's
signature so a future multi-sweep orchestrator doesn't have to fork).
The `BareFetchTracker.lock_for(url)` helper serialises
`ensure_bare → check → fetch → add` for one URL while letting threads
on different URLs proceed in parallel. On `GitError`, the URL is
left out of `tracker.fetched` so the next caller retries — same
semantics as the pre-parallel serial path. The singular's
`__call__` still accepts `bare_tracker: BareFetchTracker | None =
None`; the default allocates a fresh per-call tracker (no cross-call
dedup), which is what single-workspace callers (`add --sync`,
`import --sync`) and unit tests want. **Never reach into
`tracker.fetched` directly from a worker — always go through the use
case's `_ensure_bare_fresh` so the per-URL lock is honoured.**

## `init` vs. `adopt` vs. `import` vs. `forget`

The shared opening for bootstrap-style entry points
(`init` / clone-discovery `adopt` / `import`) — canonicalise the target
path, derive `ws_name`, raise on manifest/registry collision, write the
manifest, register — lives on `application.WorkspaceBootstrapper`.
The workspace directory itself is created as a side effect of
`ManifestRepository.write` (see "Manifest + registry split" above),
so the bootstrapper takes no `Filesystem` dep. Each lifecycle use
case constructs the bootstrapper with the same
`ManifestRepository` / `WorkspaceRegistryRepository` adapters at the
CLI composition root, then delegates by passing a
`build_manifest(ws_name) -> WorkspaceManifest` closure that owns the
caller-specific variation (e.g. `init` plugs in the `--branch` flag;
clone-discovery `adopt` plugs in the discovered repos; `import` plugs
in the external-manifest contents). Existing-manifest `adopt` uses the
same bootstrapper for path canonicalisation and registry writes, but
deliberately bypasses manifest writes so the file stays byte-for-byte
unchanged. `ForgetWorkspace` is *teardown* and does not flow through
the bootstrapper.

The bootstrapper exposes two entry points:

- **`__call__(path, build_manifest, name)`** — the do-it-all path used
  by `InitWorkspace` and `ImportWorkspace`. One `path.expanduser()
  .resolve()`, one collision check, derive `ws_name`, build the
  manifest, write, register.
- **`verify(path, name) -> (canonical, ws_name)`** +
  **`bootstrap(canonical, ws_name, manifest)`** — the canonical-in fast
  path used by clone-discovery `AdoptWorkspace`, which would otherwise
  canonicalise repeatedly. `verify` does the resolve + new-manifest
  collision check once and returns the inputs; `bootstrap` writes +
  registers without re-running the collision check. The TOCTOU window
  between the two is acceptable for the single-user CLI.
- **`verify_adopt_target(path)`** +
  **`register_existing_manifest(canonical, name)`** — the
  existing-manifest adopt path. It rejects already-registered paths but
  allows `<path>/untaped.yml`, validates that manifest, and registers
  the workspace without rewriting the file. `--name` is registry-only
  here; the manifest's `name` is otherwise the default registry name.

  `AdoptWorkspace` runs the adopt-target registry check *before* its
  `fs.exists`/`fs.is_dir` checks, so a path that's both missing on disk
  and already registered surfaces the "already registered" error rather
  than "does not exist" — pinned by
  `test_adopt_collision_check_runs_before_fs_existence_check` in
  `tests/unit/test_adopt_workspace.py`.

Four workspace lifecycle commands with deliberately distinct shapes:

- **`workspace init <name>`** — empty workspace by name; defaults the
  on-disk location to `<workspace.workspaces_dir>/<name>` (the
  `workspaces_dir` setting is profile-overridable, default
  `~/.untaped/workspaces`). Pass `-p / --path` to override the location
  for a one-off workspace that lives elsewhere. Implementation:
  `application.InitWorkspace`.
- **`workspace adopt <path>`** — claims existing workspace state. If
  `<path>/untaped.yml` exists, it validates and registers that manifest
  without rewriting it. If no manifest exists, it initialises a
  workspace from already-cloned repos: each immediate subdirectory
  containing `.git` is recorded in a new manifest with its current
  `origin` URL and checked-out branch
  (`infrastructure.LocalRepoDiscoverer` walks the directory and reads
  both via `GitRunner.read_remote_url` / `read_current_branch`; a
  detached HEAD becomes `branch: null`; missing-`origin` clones surface
  via a stderr `warn` hook). Adopted clones do not share objects with
  the bare cache — the cascade only links new clones via `git clone
  --reference`. `<path>` stays positional because adopt fundamentally
  targets an already-populated directory. Implementation:
  `application.AdoptWorkspace`.
- **`workspace import <source> <dest>`** — bootstraps a workspace
  from a local YAML manifest file (e.g. one cloned from a shared repo).
  Reads the external manifest via `ManifestRepository.read_external`
  (the narrow `ExternalManifestReader` port is what `ImportWorkspace`
  declares as its direct dep), then re-emits a manifest at `dest` whose
  `name` falls back through `--name → loaded.manifest.name →
  canonical.name`. `--sync` chains a `SyncWorkspace` call to clone the
  declared repos after registration. Implementation:
  `application.ImportWorkspace` (returns an `ImportResult` carrying
  the workspace + the imported repo names; the CLI feeds those names
  to `SyncWorkspace(..., only=…)`).
- **`workspace forget <name>`** — removes the workspace's registry
  entry only; the on-disk manifest and clones are preserved. Pass
  `--prune` to also `rmtree` the workspace directory; pruning is
  refused (mirroring `workspace remove --prune`) when any declared repo
  has uncommitted changes. A missing manifest or missing directory are
  tolerated — the registry entry is removed regardless. Implementation:
  `application.ForgetWorkspace` with explicit `Filesystem` +
  dirty-checker ports, wired to `LocalFilesystem` + `GitRunner` at the
  CLI composition root.

### `--sync` scope contract

`--sync` on lifecycle/mutation commands (`add`, `import`, and any
future `restore --sync` style flag) **syncs only the repos this
command added/changed — never the whole manifest.** Implementation:
the CLI passes `only=` to `SyncWorkspace(..., only=names)` with the
identifiers of the just-touched repos. `add --sync` passes the names
returned by `resolve_each`; `import --sync` passes
`ImportResult.repos`. If a user wants "sync everything after import",
they chain

```bash
untaped workspace import <src> <dest> && untaped workspace sync -p <dest>
```

— the lifecycle command stays focused on its own deltas.

## Repo selector strict vs. relaxed semantics

Repo-operating commands expose repeatable `--repo` / `-r` selectors. Values
match either manifest repo names or URLs. The shared application selector
lives in `application.repo_selector.select_repos` so `sync`, `status`,
`foreach`, and `branch apply` agree on matching and manifest-order output.
`SyncWorkspace.__call__` still names its internal parameter `only` because
`add --sync` and `import --sync` pass just-touched repo names through the
same use-case path.

`SyncWorkspace.__call__` takes `strict_only: bool = True`. The CLI passes
`strict_only=not all_workspaces`, so:

- **Single-workspace mode** (`sync --workspace x --repo typo`): strict. The
  use case raises `UnmatchedRepoFilter(WorkspaceError)`, a typed
  exception carrying `unmatched: tuple[str, ...]`. Callers can `except`
  precisely without parsing the error message.
- **`--all` mode** (`sync --all --repo repo-x`): relaxed. Workspaces
  whose manifests don't contain `repo-x` emit one
  `SyncOutcome(action="unmatched", repo=<identifier>, detail="not in
  this workspace's manifest")` row per requested identifier and
  continue. The discriminator lives in the type-safe `action` Literal
  (extended with `"unmatched"`) so `awk` / `cut` / `jq` consumers can
  pattern-match cleanly without overloading the `repo` data column with
  a sentinel like `<all>`.

Partial-miss is also visible: `--repo repo-x --repo typo` against
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
StubManifests, empty_manifest` — `pyproject.toml` adds this repo's
`tests/` dir to `[tool.pytest.ini_options] pythonpath` so the conftest
module is runtime-importable (pytest's `--import-mode=importlib` otherwise
hides it). New shared scaffolding for this package belongs in the same
module.

## Development Workflow

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run untaped workspace --help
```

Use `pytest --no-cov` for tight local loops. Full `pytest` enforces the
coverage gate.

## Recipe: Add A Workspace Command

1. Write a use-case test with a stub satisfying the narrowest port.
2. Add or narrow a port in `application/ports.py` if the command needs new
   service behavior.
3. Add or adjust a domain model/value object only when the behavior has pure
   workspace semantics.
4. Add infrastructure adapter methods behind existing ports for git,
   filesystem, manifest, or registry side effects.
5. Wire the Typer command in `cli/commands.py`; keep stdout data-only and
   expose `--format`/`--columns` for data output.
6. If the command emits rows, update `tests/unit/test_format_raw_first_key.py`.
7. Run `uv run untaped workspace <command> --help` plus the full verification
   commands above.

## See Also

- [`untaped` core](https://github.com/alexisbeaulieu97/untaped) - plugin
  runtime, settings registry, config-file helpers, output helpers.
- [`untaped` configuration docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md)
  - user-facing profile, config, secrets, and TLS behavior.
- [`docs/workspace.md`](docs/workspace.md) - user-facing manifest,
  registry, command reference, `uwcd` shell helper.
