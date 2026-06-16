> **Superseded.** This spec is superseded by the SDK-only direction — `untaped`
> becomes a pure SDK with no plugins, no central command, and no managed
> runtime; each tool is an independent CLI. See [decisions.md](../decisions.md).
> Retained below for historical context only.

# Managed runtime as a uv project — install & plugin UX redesign

**Status:** Design / proposed
**Date:** 2026-06-15
**Scope:** `untaped` core (installer, plugin sync, shim, install paths). No plugin-repo changes required.

## 1. Problem

Installing `untaped` so that plugins actually work today requires cloning a
checkout and running `scripts/install.sh`. That script hand-rolls a virtual
environment at `~/.local/share/untaped/venv`, drives `uv` at its **lowest**
layer (`uv venv` + `uv pip compile --no-sources` + `uv pip sync --strict`),
writes a custom PATH shim, and records a core spec in `~/.untaped/config.yml`.
`untaped plugins add/remove/sync` re-run that same low-level pipeline against
the managed venv.

Two UX problems and one architectural smell:

1. **Bootstrap requires a repo clone + script.** `uv tool install git+…` is the
   obvious one-liner, but it installs core into an isolated tool env where
   plugins can't be added incrementally.
2. **The earlier `uv tool install … --with …` approach was abandoned** because
   changing the plugin set meant re-specifying the full `--with` set and
   reinstalling core every time. (Now banned in `AGENTS.md`.)
3. **Driving `uv pip compile/sync` directly reimplements what uv already does.**
   This is the "we're bypassing uv's benefits of managing venvs automatically"
   feeling. The managed venv exists *outside* any uv-managed abstraction, so we
   own resolution orchestration, sync, and the shim by hand.

### The constraint that forces a managed runtime

`untaped` plugins load **in-process**: discovery is via `untaped.plugins` entry
points in the running interpreter, and the contract (`PluginManifest`, the
shared `untaped.api` SDK, `profile_settings`) is all in-process. In-process
plugins ⇒ **one shared environment** ⇒ **one dependency resolution** ⇒ that
environment must be **mutated** when the plugin set changes. No packaging tool
removes this; something must own a mutable runtime. This is not inherently
hacky. The hacky parts are (a) making users clone + run a script and (b)
driving uv at the pip layer.

## 2. Decision

**Keep the in-process plugin model. Make the managed runtime a real `uv`
project**, so plugin operations run on uv's *project* layer
(`uv lock` + `uv sync`) instead of the pip layer, and bootstrap becomes a
single `uvx untaped self setup`.

### Why in-process (not out-of-process / PATH-discovered executables)

Out-of-process plugins (each plugin its own isolated tool, dispatched as a
subprocess, à la `gh`/`kubectl`) would give conflict-free independent
resolution and remove the managed runtime entirely. We reject it because
`untaped`'s value is **cross-cutting consistency** — the typed `--format pipe`
NDJSON interchange, the feedback/states UX layer, unified config/profiles,
shared `render_rows`/`report_errors`/prompts. Those are free in-process and a
constant fight out-of-process, and they'd cost ~doubled cold-start latency per
command. Out-of-process is the right model for an *open ecosystem of
third-party plugins you don't control*; this is a first-party suite released in
lockstep, where co-resolution conflicts are low-risk and consistency is the
whole point.

### Rejected alternatives (and why)

- **`uv tool install … --with` / full reinstall per change** — couples every
  plugin change to a core reinstall + re-resolve; already lived and banned.
- **Mutate uv's own tool venv in place** (inject plugins into
  `~/.local/share/uv/tools/untaped/`) — uv's docs state tool environments are
  not meant to be mutated directly. `uv tool upgrade`/`--reinstall` rebuilds
  from the recorded core spec and **wipes injected plugins**; defending against
  it needs a startup drift-check + auto-resync band-aid. This trades visible
  code for a hidden, recurring failure mode (dual ownership of one directory)
  and still keeps the pip layer. Rejected.
- **uv-tool launcher + separate runtime** — installs core via `uv tool`, then a
  launcher bootstraps a *second* copy of core into the runtime (double-install,
  two roles) or requires a separate launcher package (extra artifact). Keeps
  clean ownership but adds per-invocation launcher indirection and still keeps
  the pip layer. Rejected in favor of a dumb shim.
- **`uv run --project` as the shim** — taxes every invocation (~10–50 ms env
  re-validation) and hard-depends on uv at runtime. Rejected; the shim is a
  dumb direct exec.
- **`uv add`/`uv remove` as the backend** — these mutate `pyproject.toml`
  directly, making uv a co-writer of a file we want to generate from
  `config.yml`. Forces untaped's richer state into a `[tool.untaped]` table
  (i.e. moves config into pyproject and makes the runtime project accidentally
  user-editable). Rejected in favor of untaped owning pyproject generation +
  `uv lock`/`uv sync`.

## 3. Architecture — ownership model

Four state layers plus the entry-point shim, each with exactly one owner and
one job:

| Layer | Location | Owner | Role |
|---|---|---|---|
| `config.yml` | `~/.untaped/config.yml` | **untaped** (user-facing) | declarative *intent*: plugin set, editable flags, canonical `name@url`, recorded core spec |
| `pyproject.toml` | `~/.local/share/untaped/runtime/` | **untaped** (generated) | **disposable** translation of intent into uv's input format; never hand-edited |
| `uv.lock` | `~/.local/share/untaped/runtime/` | **uv** | resolved exact pins — the **persisted** reproducibility artifact |
| `.venv` | `~/.local/share/untaped/runtime/` | **uv** | materialized env; `uv sync` makes it match the lock |
| shim | `~/.local/bin/untaped` | **untaped** | dumb `exec` to `runtime/.venv/bin/untaped`; no uv at runtime |

Key invariants:

- **`config.yml` is canonical intent.** It keeps untaped's richer semantics
  (editable flags, canonical names, auto-recorded local deps, `--no-auto-deps`)
  that a raw dependency list can't express.
- **`pyproject.toml` is disposable**, regenerated from `config.yml` on every
  mutating operation. It is never the source of truth and never hand-edited.
- **`uv.lock` is persisted, not disposable.** It is kept and only mutated by
  `uv lock` on add/remove (or with `--upgrade` on an explicit upgrade). This is
  what makes a plain `untaped plugins sync` reproducible instead of silently
  drifting to newer versions. `config.yml` = intent, `uv.lock` = realized pins.
- **Generated uv artifacts live under the data dir** (`~/.local/share/untaped/
  runtime/`), kept out of the user-facing config dir (`~/.untaped/`).
- **`XDG_DATA_HOME`/`XDG_CONFIG_HOME`** are honored as today
  (`install_paths.py` already does this for the venv root).

## 4. Commands

### `untaped self setup` — idempotent bootstrap *and* repair

Single declarative converge operation. **Not a routine command** — run once at
install, and re-runnable any time to repair a broken/missing runtime. On an
intact install it is a no-op.

Steps:
1. Record the **exact core install spec** in `config.yml` (the spec `self
   setup` was invoked with, or the running version pinned from PyPI).
2. Scaffold the runtime project dir if absent.
3. Generate `pyproject.toml` from `config.yml` (§5).
4. `uv lock --project <runtime>` (reuses existing lock as preferences).
5. `uv sync --project <runtime>`.
6. Write the dumb shim → `runtime/.venv/bin/untaped`.

Idempotency: with `config.yml`, `uv.lock`, `.venv`, and the shim all present
and consistent, steps 4–6 are no-ops (uv sync finds nothing to change; shim
write is content-identical). Repairs cover: deleted/corrupted `.venv`, missing
shim, missing/edited `pyproject.toml`, stale runtime after manual uv meddling.

### `untaped plugins add / remove`

1. Mutate `config.yml` (existing `plugin_state` logic — editable handling,
   canonical name normalization, auto-recorded local deps unchanged).
2. Regenerate `pyproject.toml` from `config.yml`.
3. `uv lock --project <runtime>` (minimal diff; existing pins preserved).
4. `uv sync --project <runtime>`.

Surface is **unchanged** from today (`add`, `remove`, `--editable`, `--stdin`,
`--no-sync`, batch specs). Only the backend changes.

### `untaped plugins sync`

Regenerate `pyproject.toml` from `config.yml` → `uv lock` (reuse existing lock
as preferences, no gratuitous upgrades) → `uv sync`. Replaces the current
`uv pip compile` + `uv pip sync --strict` path. `uv sync` provides the same
exact-match (install missing + remove extras) guarantee that `--strict` gave.

### `untaped plugins list / doctor`

Unchanged surface. `doctor` keeps a **lightweight foreign-env / stale-shim
check**: if the running interpreter is not the runtime venv (e.g. a leftover
`uv tool install untaped` shim earlier on `PATH` — a real, previously observed
breakage), warn on startup and report the fix (`untaped self setup`, or remove
the conflicting shim). The `uv-tool-upgrade-wipes-plugins` hazard does **not**
apply here because the runtime is not in uv's tool dir.

### Shared engine

`self setup`, `plugins add/remove`, and `plugins sync` all converge on one
internal **generate → lock → sync** function, serialized by the existing
`managed_env_lock` (filelock) over the runtime dir. `self setup` adds the
scaffolding + core-spec recording + shim around that engine.

## 5. `pyproject.toml` generation

Generated runtime project is a throwaway, non-packaged application project:

```toml
[project]
name = "untaped-runtime"
version = "0"
requires-python = ">=3.14"   # mirror core's requires-python (currently >=3.14)
dependencies = [
  "untaped @ git+https://github.com/.../untaped.git@v0.6.0",   # recorded core spec
  "untaped-github @ git+https://github.com/.../untaped-github.git@v0.5.1",
  "untaped-jira @ git+https://github.com/.../untaped-jira.git@v0.3.1",
  # … one entry per recorded plugin
]

[tool.uv]
package = false   # runtime project is not itself built/installed

# editable / local-path plugins map to sources derived from config.yml:
[tool.uv.sources]
untaped-profile = { path = "/abs/path/to/untaped-profile", editable = true }
```

- **Core** is the recorded core spec from `config.yml` (git, index, or
  editable), as the first dependency.
- **Each plugin** becomes a dependency line; editable/local/git entries derive
  `[tool.uv.sources]` from the config's editable flags and recorded specs.
- **No `--no-sources` needed.** A real project owns its own sources table; a
  plugin *checkout's* `[tool.uv.sources]` is irrelevant to the runtime
  project's resolution, which is exactly the property the current `--no-sources`
  hack enforces by hand.

## 6. Bootstrap UX

```bash
# If core is published to PyPI (recommended — short, memorable):
uvx untaped self setup

# Git-only distribution (copy-paste from the release page):
uvx --from "untaped @ git+https://github.com/.../untaped.git@v0.6.0" untaped self setup
```

`uvx` runs core ephemerally just long enough to execute `self setup`, which
records the exact invoking spec into `config.yml`, builds the runtime project,
locks + syncs, and writes the shim. The ephemeral copy is then discarded; the
PATH shim points at the persistent runtime. **After this, the installer is
never run again** — plugin management is pure `untaped plugins …`.

> Recommendation: publish core to PyPI so the bootstrap is `uvx untaped self
> setup`. Independent of this design, but it makes the one-liner clean and lets
> `self setup` pin `untaped==<own version>` without an explicit `--core` spec.
> This is called out as a related decision in §9, not a blocker.

## 7. Migration

- **Existing users** run `uvx untaped self setup` once. It reads the existing
  `config.yml` plugin set, builds the runtime project, and rebuilds the env.
  The old `~/.local/share/untaped/venv` can be removed (document the cleanup).
- **`config.yml` schema** is largely unchanged — it already records the core
  `PluginToolSpec` and plugin specs. The new field, if any, is the runtime
  project location (otherwise defaulted from install paths).
- **Code changes** (core only):
  - `installer.py`: replace `bootstrap_core_install`'s `uv venv` +
    `uv_pip_compile` + `uv_pip_sync` pipeline with project scaffolding +
    `uv lock` + `uv sync`. Expose it as `untaped self setup`.
  - `plugin_sync.py`: replace `uv_pip_compile_command` / `uv_pip_sync_command`
    with `uv lock` / `uv sync` command builders + a `render_pyproject(...)`
    generator (replacing `render_requirements`). Keep `managed_env_lock`,
    `validate_syncable_plugins`, `venv_python`, `require_core_spec`.
  - `install_paths.py`: add runtime project dir alongside the venv path.
  - `scripts/install.sh`: reduced to (or removed in favor of) the `uvx self
    setup` flow; keep a thin `--editable` developer entry if useful.
  - Shim writer (`write_shim`): unchanged behavior, now targets
    `runtime/.venv/bin/untaped`.
  - Foreign-env detector: keep, simplified to a `sys.prefix` vs runtime-venv
    comparison.

## 8. Edge cases & hazards

- **Stale `uv tool install untaped` shim earlier on `PATH`** → `doctor` warns +
  reports fix. (Known prior breakage; detector earns its keep.)
- **Deleted/corrupted runtime** → `untaped self setup` repairs idempotently.
- **Plugin co-resolution conflict** (two plugins pin incompatible transitive
  deps) → `uv lock` fails with a clear error. This is the inherent cost of the
  in-process model; surface a helpful message naming the conflicting packages.
  Acceptable for a lockstep-released first-party suite.
- **Explicit upgrades** → a future `untaped plugins upgrade` maps to
  `uv lock --upgrade[-package]` + `uv sync`. Out of scope here; the kept lock
  means normal `sync` never upgrades unexpectedly.
- **Windows** → `venv_python` already branches on `os.name == 'nt'`; the shim
  writer currently emits an `sh` shim and needs a Windows variant
  (`.cmd`/console script). Flagged; cross-platform shim is a follow-up.

## 9. Testing

Test through **public commands**, not internals (per repo conventions):

- **Unit:** `render_pyproject(...)` from representative `config.yml` states —
  git spec, index spec, single editable/local, multiple plugins, core editable.
  Assert generated TOML shape and `[tool.uv.sources]` derivation.
- **Integration (against a real or mocked index):**
  - `self setup` idempotency: run twice → second run changes nothing.
  - `self setup` repair: delete `.venv` / shim → re-run → restored.
  - `plugins add` → command appears (assert via `importlib.metadata` in the
    runtime venv); `plugins remove` → gone.
  - `plugins sync` reproducibility: with an unchanged `config.yml` + existing
    lock, `sync` does not bump pinned versions.
- **Lock semantics:** add plugin B after A is locked → A's pins preserved, only
  B (+ its deps) resolved.

## 10. Open / related decisions (not blockers)

- **Publish core (and plugins) to PyPI** so bootstrap is `uvx untaped self
  setup` and `self setup` can self-pin. Recommended; separable.
- **`untaped plugins upgrade`** (`uv lock --upgrade`) — future command.
- **Cross-platform shim** for Windows — follow-up.
- **Whether to retire `scripts/install.sh` entirely** vs keep a thin developer
  `--editable` shortcut — decide during implementation.
