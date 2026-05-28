# AGENTS.md — `untaped`

Single source of truth for how `untaped` core is built. AI agents and
humans both read this file; in-repo plugin internals live in
`packages/<x>/AGENTS.md`, and extracted plugins own their own root
`AGENTS.md`. If you change architecture, update the relevant file in the
same commit.

## Mission

`untaped` is a personal DevOps CLI hub. The root package owns plumbing:
the binary, plugin discovery, configuration/profile resolution, output,
stdin, HTTP/TLS, and shared errors. Domain functionality (workspace, awx,
github, profile, …) is delivered by plugins exposing Typer sub-apps
through the `untaped.plugins` entry point group. Daily DevOps work
composes — data-emitting commands are pipe-friendly. We build *on top of*
existing CLIs (`gh`, `awx-cli`) where that is the right abstraction.

## Repository Map

The workspace root **is** the `untaped` core package. Packages under
`packages/` are in-repo plugins for this bridge step; they are designed to
move to separate repos without changing the core contract.

```
untaped/
├── pyproject.toml                # workspace root + the `untaped` package
├── uv.lock                       # single shared lockfile (commit it)
├── .python-version               # 3.14
├── .pre-commit-config.yaml
├── AGENTS.md                     # ← you are here (root rules)
├── CLAUDE.md                     # imports @AGENTS.md
├── README.md                     # human-facing intro
├── docs/                         # user-facing reference
├── src/untaped/                  # core CLI, config, plugin plumbing, shared helpers
├── tests/                        # tests for core and shared contracts
└── packages/
    └── untaped-awx/              # AWX/AAP API; AGENTS.md
```

| Package             | Type | Owns                                                                  | Internals doc |
| ------------------- | ---- | --------------------------------------------------------------------- | ------------- |
| `untaped` (root)    | app/lib | Core binary, built-in `config`, plugin discovery/install/sync, settings registry, profile resolution, output/stdin/http/errors. | this file |
| `untaped-awx`       | plugin | AWX/AAP bounded context (jobs, templates, inventories, …).          | [`packages/untaped-awx/AGENTS.md`](packages/untaped-awx/AGENTS.md) |

Extracted plugins live in their own repositories and depend on the public
`untaped` plugin API. Current extracted plugins:

- [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile)
  — the `profile` command for managing the profile inventory.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github)
  — the `github` command for authenticated user and search workflows.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
  — the `workspace` command for local git workspace manifests and registry state.

## Hard Rules

Non-negotiable. Every contribution must respect them.

1. **Keep all `AGENTS.md` files up to date.** Root for cross-cutting
   rules; `packages/<x>/AGENTS.md` for domain-specific architecture. If
   you add a package, change the DDD layout, or add a cross-cutting
   helper, edit the relevant file in the same commit.
2. **Prefer `uv` commands over manual `pyproject.toml` edits.** Use
   `uv add`, `uv add --package <name>`, `uv add --group dev`,
   `uv init --package --lib|--app`. Hand-editing is fine for tool config
   (`[tool.ruff]`, `[tool.mypy]`, …) but never for dependencies.
3. **Register every new plugin through an entry point.** Use
   `[project.entry-points."untaped.plugins"]`; the root must not
   statically import plugin modules.
4. **Use `uv init --package` with `--lib` or `--app`.** Never the bare
   `uv init` (gives flat layout).
5. **Create shared plumbing only when it is genuinely cross-cutting.**
   Don't duplicate code across plugins. If two plugins need the same
   helper and it is part of the hub contract, it belongs in `src/untaped/`;
   otherwise use a separate shared library.
6. **Search before writing.** Grep `src/untaped` and other packages
   before implementing a helper. If it exists in the wrong place, *move*
   it (and update callers); don't fork.
7. **Finish each session with `uv run ruff check --fix && uv run ruff
   format`.** No exceptions.
8. **Write tests that verify the result.** TDD: failing test first, then
   implementation. Test through public APIs — never suppress warnings to
   access private members.
9. **Every Typer app and every command with required args sets
   `no_args_is_help=True`.** No-args invocation must show help, not error.
10. **Every plugin declares port `Protocol`s in `application/ports.py`.**
    Use cases import their ports from there; concrete adapters in
    `infrastructure/` satisfy the Protocols structurally (no
    inheritance). Subsystems within a domain may add their own
    (`untaped-awx` has `application/test/ports.py` for the test runner).
    Use cases declare the **narrowest port they need**; fatter ports
    extend slimmer ones via `Protocol` inheritance so concrete adapters
    satisfy every variant structurally.
11. **Mark every secret as `pydantic.SecretStr`.** Tokens, passwords, API
    keys. `untaped config list` redacts them; `repr(settings)` won't leak
    them in tracebacks. Call `.get_secret_value()` only at point of use.
12. **Use `resolve_verify(settings.http)` for every httpx client.** Never
    hard-code `verify=True/False` or a path.
13. **Use absolute imports across the workspace.** `from untaped_<x>.… import …`
    or `from untaped import …`, never `from .foo import bar`.
    Enforced by ruff's `ban-relative-imports = "all"`; applies inside
    every package, including tests.

## Architecture: Core + Plugin DDD

`untaped` core is the exception: it is the hub and shared library, not a
domain plugin. Its layout is intentionally mostly flat for plumbing
(`plugins.py`, `settings.py`, `config_file.py`, `http.py`, `output.py`,
…), with built-in `config` under `src/untaped/config/`.

Every plugin package (`untaped-<X>`) has the same internal layout:

```
src/untaped_<x>/
├── __init__.py           # re-exports `app: typer.Typer`
├── cli/                  # Typer commands (thin)
├── application/          # use cases (orchestration)
├── domain/               # entities, value objects (pure, no I/O)
└── infrastructure/       # external adapters (httpx clients, fs, …)
```

**Import direction (enforced):**

```
cli  →  application  →  domain
                 ↓
           infrastructure  →  domain
```

- `domain/` imports nothing from the other three layers — pure.
  Owns transport DTOs that cross the application/infrastructure
  boundary (in `domain/payloads.py`) so adapters need not import from
  application.
- `application/` orchestrates: declares its port `Protocol`s and any
  Callable aliases in `application/ports.py`; use cases take them via
  constructor (DI). Concrete adapters speak the port shapes
  structurally — they don't import from `application/`.
- `infrastructure/` knows about httpx, the filesystem, the config file.
- `cli/` is the thinnest layer: parse Typer args, build a use case
  (passing in concrete adapters), call it, format the result.

A use case in `application/` is unit-testable with a stub satisfying its
`Protocol` — no httpx, no fixtures, no settings file.

## Plugin Contract

Plugins expose one object through the `untaped.plugins` entry point group:

```python
class UntapedPlugin(Protocol):
    id: str
    def register(self, registry: PluginRegistry) -> None: ...
```

Available registry hooks:

- `add_cli(name, app)` adds a root command.
- `add_profile_settings(section, model)` contributes a typed profile
  settings section.
- `add_state_settings(section, model)` contributes top-level app state
  spliced into the effective settings model.
- `add_diagnostic(name, check)` contributes `untaped plugins doctor`
  checks.

Duplicate plugin ids, CLI command names, profile sections, or state
sections fail with `ConfigError`. Plugin load failures are recorded and
reported by `untaped plugins doctor`; they must not break built-in core
commands such as `untaped config`.

## Cross-Cutting helpers (`untaped`)

| Need                                       | Use                                                              |
| ------------------------------------------ | ---------------------------------------------------------------- |
| Read typed plugin config                   | `from untaped import get_config_section`                    |
| Read core settings only                    | `from untaped import get_core_settings`                     |
| Resolve TLS verify (OS trust + ca_bundle)  | `from untaped import resolve_verify`                        |
| Make an HTTP call                          | `from untaped import HttpClient`                            |
| Format output for stdout                   | `from untaped import format_output, OutputFormat`           |
| Add `--format` / `--columns` to a Typer command | `from untaped import FormatOption, ColumnsOption`      |
| Wrap a command body so `UntapedError` → exit 1 | `from untaped import report_errors`                     |
| Read piped values from stdin               | `from untaped import read_stdin`                            |
| Resolve identifiers from positionals or stdin (one source only) | `from untaped import read_identifiers` |
| Loop over identifiers with per-id `error: <id>: <exc>` rows | `from untaped import resolve_each`         |
| Parse repeated `KEY=VALUE` flags           | `from untaped import parse_kv_pairs`                        |
| Clamp `--parallel N` at an upper bound with a stderr warning | `from untaped import clamp_parallel` (caller supplies `cap` and `policy`) |
| Print a one-line message to stderr         | `typer.echo(msg, err=True)` — keep it boring; no helper          |
| Inject a stderr-warning hook into a use case | accept `warn: Callable[[str], None]` in `__init__`; `cli/` wires `typer.echo(f"warning: {msg}", err=True)` |
| Raise a typed error                        | subclass `untaped.UntapedError`                             |
| Walk the Settings schema (for tooling)     | `from untaped import walk_settings`                         |
| Register profile settings                  | `from untaped import register_profile_settings`             |
| Register top-level app state               | `from untaped import register_state_settings`               |
| Validate a Settings dict in isolation from disk/env | `from untaped import validate_settings_isolated` (used by built-in `config` write path; same shape any future read-modify-write helper needs) |
| Read/write `~/.untaped/config.yml`         | `from untaped.config_file import read_config_dict, write_config_dict, set_at_path, unset_at_path` |
| Atomic read-modify-write the config file   | `from untaped.config_file import mutate_config` (file-locked) |
| Read/write a single profile                | `from untaped.config_file import read_profile, write_profile, list_profile_names, get_active_profile_name, set_active_profile, delete_profile, rename_profile` |
| Merge `default` ⤥ active to an effective dict | `from untaped import resolve_profiles` |
| Mark a secret field                        | `pydantic.SecretStr`                                             |
| Declare port `Protocol`s for a plugin      | `packages/untaped-<x>/src/untaped_<x>/application/ports.py` (Hard Rule #10) |
| Declare DTOs that cross app/infra boundary | `packages/untaped-<x>/src/untaped_<x>/domain/payloads.py` (pydantic `BaseModel` with `frozen=True`) |

Cross-cutting subsystems with their own internals doc:

- **Configuration, profiles, plugin installs, and TLS** live in `src/untaped/`.
  User-facing reference: [`docs/configuration.md`](docs/configuration.md).
- **Workspace management** lives in the extracted
  [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
  plugin. User-facing install guidance: [`docs/workspace.md`](docs/workspace.md).
- **AWX resource framework, apply pipeline, jobs/track, test runner** —
  see [`packages/untaped-awx/AGENTS.md`](packages/untaped-awx/AGENTS.md).
  User-facing reference: [`docs/awx.md`](docs/awx.md).
- **GitHub authenticated user and search** live in the extracted
  [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github)
  plugin. User-facing install guidance: [`docs/github.md`](docs/github.md).

## Conventions

- **Module docstrings.** Every source module (`*.py`) opens with a
  module docstring describing what it owns. Re-export stubs (layer
  `__init__.py` files like `cli/__init__.py`,
  `infrastructure/__init__.py`, …) are exempt — they're plumbing,
  with nothing to describe.
- **Re-export the public surface.**
  - **Plugin packages**: `<pkg>/__init__.py` re-exports `app:
    typer.Typer`; `<pkg>/plugin.py` exposes the entry-point object that
    registers the app and config sections. Public adapters live in
    `infrastructure/__init__.py` with explicit `__all__`.
  - **`untaped`**: re-exports its public plugin/core API from
    `src/untaped/__init__.py` with explicit `__all__`.
- **Per-command flags vs shared option types.** Per-command flags in
  `cli/commands.py` use call-site defaults
  (`field: Type = typer.Option(..., "--flag", help="…")`). Shared
  option types reused across commands live in `untaped` as
  `Annotated[…, typer.Option(…)]` aliases (e.g. `FormatOption`,
  `ColumnsOption`).
- **`errors.py` placement.** Domain packages with their own exception
  subclasses keep them in a top-level `errors.py`; `untaped-awx`
  additionally has `infrastructure/errors.py` for HTTP-status →
  exception mapping. Plugins that only raise `untaped` exceptions don't
  need an `errors.py`.
- **Lazy imports on CLI startup paths.** Heavy transitive imports
  (jinja2, yaml, application use cases, infrastructure clients) that
  would pay on every `untaped --help` are deferred into subcommand
  bodies and annotated `# noqa: PLC0415`. Scope: any module reached
  on the import graph from `src/untaped/main.py` at startup (today:
  `untaped-awx/cli/test_commands.py` and `untaped-awx/cli/completions.py`).
  Enforced by ruff (`extend-select = ["PLC0415"]`); tests are exempted
  (in-function imports are idiomatic there with no startup cost). A
  bare `# noqa: PLC0415` is fine when the file's module-top comment
  carries the rationale; otherwise add a one-line inline reason
  naming the deferred cost.

## Output & Piping Conventions

- **stdout = data only.** Never print logs, prompts, or progress to
  stdout.
- **stderr = everything else.** Logs, progress, prompts. Use
  `typer.echo(msg, err=True)`.
- **Data-emitting commands** (`list`, `get`, `status`, …) expose:
  - `--format / -f` (`json | yaml | table | raw`); default `table` for
    `list`, `yaml` for `get`.
  - `--columns / -c` (repeatable). Dotted paths supported
    (`summary_fields.project.name`).
  - `--stdin` to consume newline-separated identifiers when the command
    takes a list.
- **Side-effect-only commands** (`use`, `delete`, `rename`,
  `apply --yes`, …) and interactive flows are exempt — no `--format`
  knob.
- **`--format raw` without `--columns`** emits each row's first key.
  Every list use case promises that the first key is the row's
  identifier (workspace name, job id, login, …) so pipelines like
  `untaped workspace list -f raw | xargs -I{} untaped workspace path
  {}` get the right value. Reordering keys in a row source — hand-built
  dict or pydantic model — is a contract break. Details:
  this section's `--format raw` default-column contract.
- **`--all` vs `--all-<axis>`.** Bare `--all` means "iterate every
  instance of the noun the command targets" (`workspace sync --all`,
  `workspace status --all`). When a command iterates a *different*
  axis or changes view shape, use `--all-<axis>` to disambiguate
  (`awx save --all-kinds` for the type axis, `config list
  --all-profiles` for view shape). New commands that grow an `--all`
  flag must cross-check this convention.
- **`--follow --format json` always emits NDJSON.** One bare JSON
  object per line, no enclosing array brackets — so `jq` can ingest
  the stream directly without `jq -s '.[]'`. Today: `awx jobs events
  --follow` and `awx jobs logs --follow`. New `--follow` commands that
  emit structured data under `--format json` must match this shape.
  yaml/raw/table under `--follow` is per-line single-doc emission;
  yaml has no canonical streaming form.

Pipeline examples and the morning-routine workflow live in
[`docs/README.md`](docs/README.md#pipe-friendly-by-design).

## Development Workflow

```bash
uv sync --all-packages                          # install / sync everything
uv run pre-commit install                       # local lint at commit + mypy at push
uv add --package untaped-awx httpx-retries      # runtime dep on a package
uv add --group dev some-test-helper             # dev dep on the root
uv run pytest                                   # tests with coverage (gate: 80%)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
uv run untaped --help                           # run the CLI from source
uv tool install --editable .                    # install the `untaped` binary globally
```

User-facing config, profile resolution, and the optional profile plugin
workflow are documented in [`docs/configuration.md`](docs/configuration.md).

**Coverage measurement.** `--cov` is in `addopts`, so every `pytest`
invocation measures coverage by default (gate: 80%). Two non-obvious
behaviours worth knowing:

- `pytest --collect-only` reports ~31% coverage. The cov-plugin runs
  against import-time only when collection is the only step — it's not
  a regression in real coverage. Run `pytest` (without `--collect-only`)
  for the real number.
- For tight TDD loops, `pytest --no-cov` skips coverage measurement
  and shaves a few hundred milliseconds per run.

**TDD loop:**
1. Write the failing test (`tests/unit/` for core, or
   `packages/untaped-<x>/tests/unit/` for an in-repo plugin).
2. Run it; confirm it fails for the right reason.
3. Implement the smallest change that makes it pass.
4. Refactor with the test still green.

**Test layout:**
- Core tests live in `tests/unit/`. In-repo plugin tests live in
  `packages/<pkg>/tests/unit/` by default. Use
  `tests/integration/` when the test exercises a real subprocess or
  fake-server fixture (`untaped-awx`'s `FakeAap`). Pure use-case tests
  with stubs stay in `unit/`.
- No `__init__.py` files inside `tests/` — pytest uses
  `--import-mode=importlib`.
- Mock httpx with `respx` (already a dev dep).
- For CLI tests, use `typer.testing.CliRunner`.

**Bridge-step plugin tests:** while some plugins still live under
`packages/`, root tests may dynamically import local plugin packages only
to preserve legacy coverage and verify in-monorepo entry-point
registration. Core plugin-loading behavior must be tested with fake
plugins, and production `src/untaped/` must stay free of static plugin
imports. Before extracting another plugin, move its plugin-specific tests
and AGENTS guidance into the target repository.

## Decision Tree: Where does this code go?

1. **Shared across two or more plugins?** → `src/untaped/` if it is hub
   plumbing, or a separate shared lib if it is a coherent subdomain.
2. **CLI-only (argument parsing, output formatting)?** → `cli/` inside
   the domain.
3. **Pure business logic — entities, value objects, invariants?** →
   `domain/`.
4. **Talks to an external service (HTTP, fs, subprocess)?** →
   `infrastructure/`.
5. **Orchestrates steps between domain and infrastructure?** →
   `application/`.

## Recipe: Add a new in-repo plugin package

```bash
# 1. Create the package
uv init --package --lib packages/untaped-<X>
# 2. Add deps
uv add --package untaped-<X> typer untaped
# 3. Build the 4 layers
mkdir -p packages/untaped-<X>/src/untaped_<x>/{cli,application,domain,infrastructure}
mkdir -p packages/untaped-<X>/tests/unit
```

Then:
- Implement `domain/models.py`, `infrastructure/<x>_client.py`,
  `application/<use_case>.py`, `cli/commands.py`. Add `@app.callback()`
  (single-command Typer apps collapse without it).
- Re-export `app` from `cli/__init__.py` and the package `__init__.py`.
- Add `plugin.py` and register the Typer app/config sections:
  ```python
  from untaped.plugins import PluginRegistry
  from untaped_<x> import app

  class XPlugin:
      id = "<x>"
      def register(self, registry: PluginRegistry) -> None:
          registry.add_cli("<x>", app)

  plugin = XPlugin()
  ```
- Add the entry point to the package `pyproject.toml`:
  ```toml
  [project.entry-points."untaped.plugins"]
  <x> = "untaped_<x>.plugin:plugin"
  ```
- Append `untaped_<x>` to `[tool.untaped].plugin_modules` in the root
  `pyproject.toml`, then run
  `uv run python scripts/sync_plugins.py --write`. The script
  regenerates the four `[tool.importlinter]` lists and the
  `[tool.mypy] packages` list from that single source of truth; the
  sync check pre-commit hook flags drift on commit.
- Add the package to this file's Repository Map.
- **Create `packages/untaped-<X>/AGENTS.md`** for domain-specific
  internals (resource framework, side-effect adapters, polling cadence,
  …) plus a short `CLAUDE.md` stub: `See @AGENTS.md for <pkg>
  internals. For workspace-wide rules see @../../AGENTS.md.`
- Run `uv sync && uv run pytest && uv run untaped --help`. If you have
  the global tool installed, re-run `uv tool install --editable .`.

## Recipe: Add a new command to an existing plugin

1. **Test first** in `packages/untaped-<X>/tests/unit/test_<feature>.py`.
2. New external call → add a method to the existing
   `infrastructure/<x>_client.py`. Don't create a new client class
   unless it's a different service.
3. New domain logic → add an entity/method in `domain/`, a use case in
   `application/`.
4. Add the Typer command in `cli/commands.py`:
   - decorate with `@app.command(..., no_args_is_help=True)` if it has
     required args
   - log to stderr; print only data to stdout
   - if it emits data: accept `--format` and `--columns`; support
     `--stdin` if it takes a list
   - pure side-effect commands and interactive flows can skip the
     pipe-friendly knobs
5. Run tests, lint, format. Verify:
   `uv run untaped <X> <new-command> --help`.

## Recipe: Add a new setting

Core settings (`log_level`, `http`) live in `src/untaped/settings.py`.
Plugin settings live with the plugin and are registered from
`plugin.py` with `add_profile_settings` and/or `add_state_settings`.
Credentials must be `SecretStr`; HTTP clients must still consume
`resolve_verify(get_core_settings().http)`.

## Common Mistakes

- **Importing httpx, pyyaml, or os in a `domain/` module.** Domain is
  pure. Move the call to `infrastructure/`.
- **A `cli/` module calling business logic from `infrastructure/`
  instead of going through an `application/` use case.** Wiring concrete
  adapters at the composition root (e.g. `cli/_context.py`) is fine; the
  ban is on bypassing application use cases for the actual logic.
- **Adding a new dep with `pyproject.toml` edits.** Use `uv add --package`.
- **Writing a helper inside a plugin that another plugin will need.**
  Move it to `src/untaped/` only if it is hub plumbing; otherwise use a
  separate shared lib (move ≠ copy).
- **Forgetting the plugin entry point.** Test: does
  `uv run untaped --help` list the plugin command after the package is
  installed in the environment?
- **Adding a single-command Typer app without `@app.callback()`.** Typer
  collapses single-command apps into a flat command, breaking subcommand
  dispatch from the root CLI.
- **Adding a new setting without thinking about secrets and TLS.**
  Credentials → `pydantic.SecretStr`. Hostnames for TLS services → the
  client must use `resolve_verify(s.http)`.
- **Forgetting `no_args_is_help=True` on commands with required args.**
- **Naming a method `list` on a class whose annotations elsewhere include
  `list[X]`.** mypy resolves `list` to the method, not the builtin. Use
  `entries` instead, or `Iterator[X]` / `Iterable[X]` returns (no
  collision).
- **Changing workspace git semantics in core.** Workspace git operations
  live in the extracted `untaped-workspace` plugin; update that repo's
  `AGENTS.md` and tests with any workspace behavior change.

## See also

- **Per-package internals**:
  [`untaped-awx`](packages/untaped-awx/AGENTS.md)
- **Extracted plugins**:
  [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile),
  [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github),
  [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
- **User-facing docs**: [`docs/`](docs/README.md) — configuration,
  workspaces, AWX, GitHub
