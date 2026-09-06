# AGENTS.md — `untaped` (unified app v4)

Single source of truth for how the unified `untaped` application is built.
AI agents and humans both read this file. This repo is **one modular
application**: a single `untaped` console script composing built-in
capabilities. The standalone `untaped-*` tool repos are retired at the v4
cutover; there is no multi-repo workspace guidance here anymore.

## Mission

`untaped` is a single batteries-included CLI built on cyclopts: config,
profiles, themes, consistent output, typed piping, HTTP/TLS, and UI/prompt
helpers, plus one command subtree per built-in capability
(`untaped workspace ...`, ...). Composition runs through
`src/untaped/bootstrap.py` (`main()`), which discovers built-in
capability specs plus externals via the `untaped.capabilities` entry-point
group, validates them through the registry, then mounts the survivors.
See [`docs/capabilities-spec.md`](docs/capabilities-spec.md) for the
authoritative composition contract and [`docs/decisions.md`](docs/decisions.md)
for the ADRs behind the v4 direction.

## Repository Map

```
untaped/  (repo root IS the app; version 4.0.0rc1, requires-python >=3.14)
├── pyproject.toml                # single `untaped` package; script `untaped = untaped.__main__:main`
├── uv.lock                       # lockfile (commit it)
├── AGENTS.md                     # ← you are here (unified-app rules)
├── docs/                         # capabilities-spec.md (contract), decisions.md (ADRs), …
├── src/untaped/
│   ├── __main__.py               # entry point → bootstrap.main
│   ├── bootstrap.py              # composition root (replaces run/tool path)
│   ├── capability_api.py         # STABLE provider import surface (spec §2)
│   ├── capabilities/
│   │   ├── registry.py           # internal composition kernel (NOT re-exported)
│   │   └── <name>/               # one dir per built-in capability (only `workspace` today)
│   ├── management/               # root commands: config, profile, skills, doctor, capabilities
│   └── <core>/                   # config, profile, cli, api, settings, http, ui, … (shared framework)
└── tests/                        # app tests
```

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

- `capability_api.py` is the **only** module provider code imports from:
  spec types (`ApplicationSpec`, `CapabilitySpec`, `SkillAsset`,
  `DoctorCheck`, `DoctorResult`, `CapabilityContext`), the shared helpers,
  and `CAPABILITY_API_VERSION` (currently `1.0`; built-ins declare
  `api_requires` in `(1.0, 2.0)`).
- `capabilities/registry.py` is the internal composition kernel: discovery /
  API pre-checks → provider resolution → declaration validation + app-factory
  staging → commit. Built-in violations raise `ConfigError` (fatal);
  external violations become `QuarantineRecord` entries while composition
  continues. Provider authors never import it.
- Reserved root names no capability may claim: `profiles`, `active`,
  `config`, `profile`, `skills`, `doctor`, `capabilities`.
- A new built-in capability: add `capabilities/<name>/` per the layout
  above, expose `SPEC` + `build_app`, and append it to
  `BUILTIN_CAPABILITIES` in `bootstrap.py` in declaration order.

## Management commands

The root shell owns five management commands in `management/` (built from
shared core logic, with only root-specific resolution rules as new private
helpers): `untaped config …`, `untaped profile …`, `untaped skills …`,
`untaped doctor` (offline checks, per-capability rows isolated), and
`untaped capabilities` (one record per candidate provider, ready or
quarantined). Config diagnostics live at root `doctor`, not under
`config doctor`.

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
Use `untaped-orchestration` for canonical reads and mutations, including revision guards
on every mutation. Agents never use `--force-current`. The committed views are
human-only generated state, not canonical agent input. After hand recovery, run
`untaped-orchestration check --local` and `untaped-orchestration render --check`.

## Wave gates

Capability slices land serialized, one at a time, each gated before the
next starts:

1. Fresh disposable checkout implementing exactly one slice per its spec.
2. Senior review: PASS (or fix-and-re-review). Involved lines only.
3. Cumulative checks on every accepted slice, un-narrowed: FULL suite,
   `ruff`, `mypy`, and the S-suite scanners that run in Wave 1 CI
   (see `.github/workflows/ci.yml`).
4. Accepted slice OID recorded in the control root.
5. No remote publication without explicit authorization.

## See also

- **Composition contract:** [`docs/capabilities-spec.md`](docs/capabilities-spec.md)
- **Decisions (ADRs):** [`docs/decisions.md`](docs/decisions.md)
- **User-facing docs:** [`docs/`](docs/README.md)
