# AGENTS.md — `untaped` (unified app)

Contribution rules for the unified `untaped` application.
AI agents and humans both read this file. This repo is **one modular
application**: a single `untaped` console script composing first-party
capabilities. This repository contains the application and its first-party
capabilities; there is no multi-repo workspace guidance here.

## Mission

`untaped` is a single batteries-included CLI built on cyclopts: config,
profiles, themes, consistent output, typed piping, HTTP/TLS, and UI/prompt
helpers, plus one command subtree per first-party capability
(`untaped workspace ...`, ...). Composition runs through
`packages/untaped/src/untaped/bootstrap.py` (`main()`), which discovers every capability,
first-party ones included, through the `untaped.capabilities` entry-point
group, validates them through the registry, then mounts the survivors. The
implementation in `packages/untaped/src/untaped/` is authoritative for composition and command
behavior. User workflows live in [`docs/`](docs/getting-started.md); provider authors
should start with [`docs/plugins.md`](docs/plugins.md).

Inspect `untaped capabilities` for the current first-party capabilities.
The source tree is the implementation reference:

- The root `pyproject.toml` is the uv workspace root (tool configuration and
  the `dev` group, no `[project]`); `uv.lock` locks the whole workspace.
- `packages/<name>/` holds one distribution each: its `pyproject.toml`,
  `src/` and `tests/`. Core is `packages/untaped/`: its `src/untaped/`
  contains the shell and shared services. Each capability is its own package,
  `packages/untaped-<name>/src/untaped_<name>/`, and owns one capability end to end.
- `examples/untaped-hello/` is a minimal third-party plugin with its own
  tests; it is not a workspace member and is never published. CI installs it
  beside the core wheel and runs its tests outside the repository.
- `docs/` contains user guides and executable policy files.
- `tests/` verifies public behavior and release contracts: `tests/repo/` holds
  the cross-package tests and `tests/skills/` the skill checks; a capability's
  tests live in `packages/untaped-<name>/tests/<name>/`.

A capability owns its directory end to end:

```
packages/untaped-<name>/src/untaped_<name>/
├── __init__.py        # SPEC: CapabilitySpec (with one-line help) + nullary build_app() (lazy CLI import; never build at import time) + provider()
├── settings.py        # profile model + state model (field sets must be disjoint)
├── api.py             # optional: declared public module other first-party capabilities may import (Hard Rule 2)
├── cli/               # cyclopts commands (thin)
├── application/       # use cases (orchestration); ports in application/ports.py
├── domain/            # entities, value objects (pure, no I/O)
├── infrastructure/    # external adapters (httpx clients, fs, …)
├── errors.py          # <Cap>Error(UntapedError) and its subclasses
└── skills/            # packaged agent skills shipped via SPEC.skills
```

Import direction inside a capability: `cli → application → domain` and
`infrastructure → domain`; `domain/` imports nothing from the other layers.

Commands follow [the conventions](docs/plugins.md#conventions) (flags,
messages, exit codes, record shapes) through the `untaped.sdk` helpers it
lists: `UsageError`, shared option aliases, `plural`/`q`/`not_found`/`hint`,
`ui.success`, `batch_apply`/`ui.confirm_action`, `read_identifiers(accept_kinds=…)`,
and the `OutcomeRecord`/`TargetRecord` bases. Each capability's tests run
`untaped.testing.check_conventions`. Every error raises with
a `category` and `system` (class defaults in the capability's `errors.py`) and
keeps that attribution when replaced or turned into a row; see
[Raise with a category](docs/plugins.md#raise-with-a-category-or-inherit-one).

## Capability registry + the SDK (`untaped.sdk`)

- `sdk.py` is the single public SDK surface and the **only** core module
  capability code (first-party or third-party) imports from; first-party
  capabilities may also use another capability's declared `api.py` (Hard
  Rule 2). Its exported types and helpers are the provider API; a provider's
  `untaped` requirement (`Requires-Dist`) is the only compatibility check;
  the package root re-exports nothing.
- `capabilities/registry.py` is the internal composition kernel: discovery and
  metadata pre-checks → provider resolution → declaration validation → commit.
  Every violation becomes a `QuarantineRecord` entry while composition
  continues. Provider authors never import it.
- Management command names are owned by the root shell; inspect
  `untaped --help` and `packages/untaped/src/untaped/management/` when adding a capability.
- A new first-party capability: add `packages/untaped-<name>/` per the layout
  above, expose `SPEC`, `build_app` and a nullary `provider()` returning
  `SPEC`, and add `<name> = "untaped_<name>:provider"` under
  `[project.entry-points."untaped.capabilities"]` in its `pyproject.toml`
  (copy a sibling package's; add the `untaped[<name>]` extra to core and a
  `[tool.uv.sources]` entry, then `uv sync`). Also add the package to the
  root `pyproject.toml` lists (mypy `files`/`mypy_path`, pytest
  `testpaths`/`pythonpath`, coverage `source`, `[tool.uv.sources]`), to
  `EXPECTED_MEMBERS` in `tests/repo/test_workspace.py` and to `FIRST_PARTY` in
  `tests/repo/support.py`; the tests catch omissions. Start its
  skill from the [skill template](docs/plugins.md#packaged-skills) (which holds
  the skill rules) and its user guide at `docs/<name>/usage.md`, linked from
  the root `README.md` and `docs/getting-started.md`. Set
  `SPEC.help` to the app's one-line help: a capability with `help` is mounted
  lazily (its factory runs on first dispatch, and in `untaped doctor`), so
  `untaped --help` and other capabilities never import its CLI. Without
  `help` the factory runs during composition.

## Management commands

The root shell owns management commands in `management/`, built from shared
core logic with only root-specific resolution rules as private helpers. Read
the current command names and options from `untaped --help`; config
diagnostics live at root `doctor`.

## Config & state model

Settings live in profiles in `config.yml`; capability state lives in
`state.yml`, read and written only through `StateCollection`/`StateMap`; see
[`docs/configuration.md`](docs/configuration.md).
[`docs/reference/config.md`](docs/reference/config.md) is generated from the
settings models: after changing one, run
`uv run python scripts/gen_config_reference.py` (a test fails while it is
stale).

## Per-capability ownership (Hard Rules)

Non-negotiable. Every contribution must respect these plus the workflow
rules below.

1. **One owner per section.** A capability owns its config section, its
   profile/state models, its skills, and its doctor checks. Never read or
   write another capability's section; never mutate another capability's
   state.
2. **Cross-capability code goes through a declared public module.**
   Capability code imports core only from `untaped.sdk`. It may
   import another capability only through that capability's public module,
   `<package>.api` (for example `untaped_github.api`), never its other internals; the
   importing package declares a dependency on the other package;
   `check_conventions` enforces both.
   Dependencies are one-way (no cycles). Import them lazily on CLI paths;
   the one exception is a settings model that validates against the other
   capability, which imports it at module top.
   Logic two capabilities need lives in exactly one owner's `api.py` or in
   core — never forked into both; extract a protocol into core only when a
   second provider appears.
3. **Keep `AGENTS.md` and `docs/` up to date.** If you change the
   composition contract, a management workflow, or a cross-cutting helper,
   edit the relevant docs in the same commit. Each fact has one home, and
   other pages link to it: capability detail in its skill (`usage.md` is a
   short guide), exit codes/settings/record kinds/env vars in `docs/reference/`,
   history in `CHANGELOG.md`, rationale in `.planning/decisions/`. Never copy
   `--help` output, default columns, or API signatures into docs.
4. **Involved-lines-only diffs.** Touch only the lines your change
   requires; no drive-by refactors, no unrelated file churn.

## Development Workflow

```bash
uv sync                                         # install / sync the app
uv run pytest -n auto                           # tests, in parallel (add `--cov` for the 89% coverage gate, as CI does)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
uv run pre-commit run --all-files               # pre-commit hooks
```

TDD: failing test first, then the smallest implementation. Test through
public APIs — never suppress warnings to access private members. Grep
before writing: if a helper exists in the wrong place, *move* it (and
update callers); don't fork. Every module opens with a docstring describing
what it owns (re-export stubs exempt). Lazy imports on CLI startup paths
(`# noqa: PLC0415` only where Ruff flags it). Absolute imports only
(`ban-relative-imports = "all"`, tests included). Secrets are
`pydantic.SecretStr`; HTTP clients resolve TLS via `resolve_verify`. Git
subprocesses go through `untaped.git` (`run_git`, `git_toplevel`, re-exported by
`untaped.sdk`); never fork your own `subprocess` git plumbing.
Bare-repo caches go through `untaped.sdk.RepoCache`; never fork cache plumbing.
Advisory lock files go through `untaped.fs` (`file_lock`, re-exported by
`untaped.sdk`).

Releasing: see [`docs/release.md`](docs/release.md) (a release PR, a
TestPyPI rehearsal, then a `vX.Y.Z` tag on main).

## Planning and decisions

Read relevant constraints in [`.planning/decisions/`](.planning/decisions/)
before changing architecture. Code and tests define implementation behavior;
keep decisions short and focused on rationale and constraints.

Tasks, roadmap, priorities, blockers, and handoffs live in private GitHub Issues
and the private Untaped Project owned by `untaped-private`. Its `AGENTS.md`
describes the workflow. Do not copy private task bodies or planning exports into
this public repository. Do not maintain a second backlog or generated roadmap.
Use Superpowers for design, implementation, and review. Put public behavioral
changes and their validation in the implementation PR.

Existing user authorization persists for its concrete scope. A backlog item
alone does not authorize remote publication or unrelated work.

## See also

- **Provider authoring:** [`docs/plugins.md`](docs/plugins.md)
- **User-facing docs:** [`docs/`](docs/getting-started.md)
