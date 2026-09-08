# AGENTS.md — `untaped` (unified app v4)

Contribution rules for the unified `untaped` application.
AI agents and humans both read this file. This repo is **one modular
application**: a single `untaped` console script composing built-in
capabilities. This repository contains the application and its built-in
capabilities; there is no multi-repo workspace guidance here.

## Mission

`untaped` is a single batteries-included CLI built on cyclopts: config,
profiles, themes, consistent output, typed piping, HTTP/TLS, and UI/prompt
helpers, plus one command subtree per built-in capability
(`untaped workspace ...`, ...). Composition runs through
`src/untaped/bootstrap.py` (`main()`), which discovers built-in
capability specs plus externals via the `untaped.capabilities` entry-point
group, validates them through the registry, then mounts the survivors. The
implementation in `src/untaped/` is authoritative for composition and command
behavior. User workflows live in [`docs/`](docs/README.md); provider authors
should start with [`docs/plugins.md`](docs/plugins.md).

Inspect `untaped --help` for the current built-in command order. The source
tree is the implementation reference:

- `pyproject.toml` and `uv.lock` define the distribution and locked
  environment.
- `src/untaped/` contains the shell, shared services, and built-in
  capabilities. Each `src/untaped/capabilities/<name>/` directory owns one
  capability end to end.
- `docs/` contains user guides and executable policy files.
- `tests/` verifies public behavior and release contracts.

A capability owns its directory end to end:

```
src/untaped/capabilities/<name>/
├── __init__.py        # SPEC: CapabilitySpec + nullary build_app() (lazy CLI import; never build at import time)
├── settings.py        # profile model + state model (field sets must be disjoint)
├── cli/               # cyclopts commands (thin)
├── application/       # use cases (orchestration); ports in application/ports.py
├── domain/            # entities, value objects (pure, no I/O)
├── infrastructure/    # external adapters (httpx clients, fs, …)
├── errors.py          # capability exception subclasses (only if it defines its own)
└── skills/            # packaged agent skills shipped via SPEC.skills
```

Import direction inside a capability: `cli → application → domain` and
`infrastructure → domain`; `domain/` imports nothing from the other layers.

## Capability registry + capability_api

- `capability_api.py` is the **only** module provider code imports from. Its
  exported types, helpers, and API version are the source of truth for
  provider compatibility.
- `capabilities/registry.py` is the internal composition kernel: discovery /
  API pre-checks → provider resolution → declaration validation + app-factory
  staging → commit. Built-in violations raise `ConfigError` (fatal);
  external violations become `QuarantineRecord` entries while composition
  continues. Provider authors never import it.
- Management command names are owned by the root shell; inspect
  `untaped --help` and `src/untaped/management/` when adding a capability.
- A new built-in capability: add `capabilities/<name>/` per the layout
  above, expose `SPEC` + `build_app`, and append it to
  `BUILTIN_CAPABILITIES` in `bootstrap.py` in declaration order.

## Management commands

The root shell owns management commands in `management/`, built from shared
core logic with only root-specific resolution rules as private helpers. Read
the current command names and options from `untaped --help`; config
diagnostics live at root `doctor`.

## Per-capability ownership (Hard Rules)

Non-negotiable. Every contribution must respect these plus the workflow
rules below.

1. **One owner per section.** A capability owns its config section, its
   profile/state models, its skills, and its doctor checks. Never read or
   write another capability's section; never mutate another capability's
   state.
2. **No cross-capability private-helper coupling.** Capability code imports
   shared code only from `untaped.capability_api` (or core framework
   modules through it). Never import a sibling capability's private
   helpers (`from untaped.capabilities.<other>...` except through the
   owning capability's public SPEC surface). If two capabilities need the
   same logic, it belongs in core (exposed via `capability_api`) or in
   exactly one owning capability — never forked into both.
3. **Keep `AGENTS.md` and `docs/` up to date.** If you change the
   composition contract, a management workflow, or a cross-cutting helper,
   edit the relevant docs in the same commit.
4. **Involved-lines-only diffs.** Touch only the lines your change
   requires; no drive-by refactors, no unrelated file churn.

## Development Workflow

```bash
uv sync                                         # install / sync the app
uv run pytest                                   # tests with coverage (gate: 80%)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
```

TDD: failing test first, then the smallest implementation. Test through
public APIs — never suppress warnings to access private members. Grep
before writing: if a helper exists in the wrong place, *move* it (and
update callers); don't fork. Every module opens with a docstring describing
what it owns (re-export stubs exempt). Lazy imports on CLI startup paths
(`# noqa: PLC0415` only where Ruff flags it). Absolute imports only
(`ban-relative-imports = "all"`, tests included). Secrets are
`pydantic.SecretStr`; HTTP clients resolve TLS via `resolve_verify`.

## Orchestration store

The repository has a public decision-only orchestration store; it contains no tasks.
Use the unified `untaped orchestration` capability for canonical reads and mutations, including revision guards
on every mutation. Agents never use `--force-current`. The committed views are
human-only generated state, not canonical agent input. After hand recovery, run
`untaped orchestration check --local` and `untaped orchestration render --check`.

Current architectural decisions are recorded in the public store under
`.untaped/orchestration`. Sequencing and release planning belong in the private
`untaped-private` planning repository; private roadmap or hub prose is not
mirrored here. Keep public records free of private planning details.

Do not publish remotely without explicit authorization.

## See also

- **Provider authoring:** [`docs/plugins.md`](docs/plugins.md)
- **User-facing docs:** [`docs/`](docs/README.md)
