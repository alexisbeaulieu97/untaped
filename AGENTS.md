# AGENTS.md — `untaped`

Single source of truth for how `untaped` core is built. AI agents and
humans both read this file. Plugin internals live in their own repos and
own `AGENTS.md` files. If you change architecture, update the relevant
file in the same commit.

## Mission

`untaped` is a personal DevOps CLI hub. The root package owns plumbing:
the binary, plugin discovery, configuration/profile resolution, output,
stdin, HTTP/TLS, and shared errors. Domain functionality (workspace, awx,
github, profile, …) is delivered by plugins exposing Cyclopts sub-apps
through the `untaped.plugins` entry point group. Daily DevOps work
composes — row-oriented `list`/`get`/`status`-style commands are
pipe-friendly. We build *on top of* existing CLIs (`gh`, `awx-cli`) where
that is the right abstraction.

## Repository Map

The workspace root **is** the `untaped` core package. Domain plugins live
in separate repositories and integrate through entry points.

```
untaped/
├── pyproject.toml                # the `untaped` package
├── uv.lock                       # lockfile (commit it)
├── .python-version               # 3.14
├── .pre-commit-config.yaml
├── AGENTS.md                     # ← you are here (root rules)
├── CLAUDE.md                     # imports @AGENTS.md
├── README.md                     # human-facing intro
├── docs/                         # user-facing reference
├── src/untaped/                  # core CLI, config, plugin plumbing, shared helpers
└── tests/                        # tests for core and shared contracts
```

| Package             | Type | Owns                                                                  | Internals doc |
| ------------------- | ---- | --------------------------------------------------------------------- | ------------- |
| `untaped` (root)    | app/lib | Core binary, built-in `config`, plugin discovery/install/sync, settings registry, profile resolution, output/stdin/http/errors. | this file |

Plugins live in their own repositories and depend on the public `untaped`
plugin API. Current plugins:

- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx)
  — the `awx` command for Ansible Automation Platform / AWX workflows.
- [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible)
  — the `ansible` command for Ansible dependency graph workflows.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github)
  — the `github` command for authenticated user and search workflows.
- [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira)
  — the `jira` command for Jira Data Center workflows.
- [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile)
  — the `profile` command for managing the profile inventory.
- [`untaped-themes`](https://github.com/alexisbeaulieu97/untaped-themes)
  — terminal theme presets for semantic UI rendering.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
  — the `workspace` command for local git workspace manifests and registry state.

## Hard Rules

Non-negotiable. Every contribution must respect them.

1. **Keep all `AGENTS.md` files up to date.** Root for core and
   cross-cutting rules; plugin repos for domain-specific architecture.
   If you change the plugin contract, a DDD layout, or a cross-cutting
   helper, edit the relevant file in the same commit.
2. **Prefer `uv` commands over manual `pyproject.toml` edits.** Use
   `uv add`, `uv add --package <name>`, `uv add --group dev`,
   `uv init --package --lib|--app`. Hand-editing is fine for tool config
   (`[tool.ruff]`, `[tool.mypy]`, …) but never for dependencies.
3. **Register every new plugin through an entry point.** Use
   `[project.entry-points."untaped.plugins"]`; the root must not
   statically import plugin modules.
4. **Use `uv init --package` with `--lib` or `--app` for new plugin
   repos.** Never the bare `uv init` (gives flat layout).
5. **Create shared plumbing only when it is genuinely cross-cutting.**
   Don't duplicate code across plugins. If two plugins need the same
   helper and it is part of the hub contract, it belongs in `src/untaped/`;
   otherwise use a separate shared library.
6. **Search before writing.** Grep `src/untaped` and relevant plugin
   repos before implementing a helper. If it exists in the wrong place,
   *move* it (and update callers); don't fork.
7. **Finish each session with `uv run ruff check --fix && uv run ruff
   format`.** No exceptions.
8. **Write tests that verify the result.** TDD: failing test first, then
   implementation. Test through public APIs — never suppress warnings to
   access private members.
9. **Cyclopts command signatures are explicit.** Use
   `Annotated[..., Parameter(...)]` for options/arguments and name public
   commands/options explicitly. Required inputs are required
   positional-only parameters (`Parameter(help=...)`, no `name=`, declared
   before `/`); a missing value renders `error: ... requires an argument`
   on stderr with exit 2 via `run_cyclopts_app` — never emulate it with an
   optional default plus a manual help dance.
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
13. **Use absolute imports.** `from untaped import …`, never
    `from .foo import bar`. Enforced by ruff's
    `ban-relative-imports = "all"`; applies to tests too.

## Architecture: Core + Plugin DDD

`untaped` core is the exception: it is the hub and shared library, not a
domain plugin. Its layout is intentionally mostly flat for plumbing
(`plugins.py`, `settings.py`, `config_file.py`, `http.py`, `output.py`,
…), with built-in `config` under `src/untaped/config/`.

Every plugin package (`untaped-<X>`) has the same internal layout:

```
src/untaped_<x>/
├── __init__.py           # re-exports `app: cyclopts.App`
├── cli/                  # Cyclopts commands (thin)
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
- `cli/` is the thinnest layer: parse Cyclopts args, build a use case
  (passing in concrete adapters), call it, format the result.

A use case in `application/` is unit-testable with a stub satisfying its
`Protocol` — no httpx, no fixtures, no settings file.

## Plugin Contract

Plugins expose one object through the `untaped.plugins` entry point group and
import the SDK surface from `untaped.api` (the supported plugin API; core
internals stay free to move as long as `untaped.api` keeps resolving).

**Preferred (api version 3, declarative):** the object defines `id`, literal
`untaped_api_version = 3`, and `manifest() -> PluginManifest`. The manifest is
data (`clis`, `profile_settings`, `state_settings`, `themes`, `skills`,
`diagnostics`); core validates it as a whole and commits it atomically, so a
conflicting manifest contributes nothing. `CliSpec(name=..., import_path=
"untaped_<x>.cli:app", help=...)` defers importing the CLI module until the
command is dispatched — root `--help` lists the command from the spec's
`help` text without paying the import.

```python
from untaped.api import CliSpec, PluginManifest

class XPlugin:
    id = "<x>"
    untaped_api_version = 3

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            clis=(CliSpec(name="<x>", import_path="untaped_<x>.cli:app", help="..."),),
            profile_settings={"<x>": XSettings},
        )
```

**Legacy (api version 2, imperative):** `untaped_api_version = 2` plus
`register(self, registry: PluginRegistry) -> None` calling the registry hooks
below. Still accepted; new plugins use manifests.

The plugin object must declare the API version as a literal. Do not import a
core constant for this value; future cores use the literal to reject plugins
built for an incompatible plugin API before running registration hooks.
Package dependency bounds still matter for import-time failures, while the
runtime API version catches registration-contract mismatches and reports them
through `untaped plugins doctor`.

Available registry hooks (manifest fields map 1:1 onto these):

- `add_cli(name, app)` adds a root command.
- `add_profile_settings(section, model)` contributes a typed profile
  settings section.
- `add_state_settings(section, model)` contributes top-level app state
  spliced into the effective settings model.
- `add_theme(name, spec)` contributes a named `ThemeSpec` preset for
  terminal rendering. Theme plugins register presets only; core owns the
  renderer and still keeps `json`, `yaml`, and `raw` pipe-friendly.
  `ThemeSpec.color_roles` accepts Rich style strings for `header`,
  `border`, `key`, `value`, `success`, `info`, `warning`, and `error`.
  Core emits those styles only for interactive terminal output; redirected
  output remains plain text.
- `add_skill(spec)` contributes a packaged agent skill directory. Skill
  names must be `untaped` or start with `untaped-`; the source directory
  must contain a valid `SKILL.md`. Core owns `untaped skills list/install`
  and plugins only register static skill assets.
- `add_diagnostic(name, check)` contributes `untaped plugins doctor`
  checks.

There is intentionally no prompt-backend registry hook yet. Core owns prompt
primitives and backs them with `prompt_toolkit`; plugins should call the core
prompt helpers instead of importing CLI framework prompt helpers, or
`prompt_toolkit` directly.

Duplicate plugin ids, CLI command names, profile sections, state sections,
diagnostics, theme names, or skill names fail with `ConfigError`. Built-in
theme and skill names are reserved and cannot be shadowed by plugins.
Plugin load failures are recorded and reported by `untaped plugins doctor`;
they must not break built-in core commands such as `untaped config`.

Plugin install specs are keyed by normalized package/plugin name. Direct
URL convenience input (`git+https://.../untaped-profile.git`) is accepted
only when the name can be inferred from the final path segment, then stored
canonically as `untaped-profile @ git+https://...`. Removal and replacement
must work by that normalized name, including for legacy bare URL state.
`untaped plugins list` is a row-oriented data command and follows the shared
`--format` / `--columns` contract; its first row key is `name` so
implicit `--format raw` is pipe-friendly for `untaped plugins remove`.
Implicit raw output emits only removable recorded packages; loaded-only rows
remain visible in table/json/yaml output or when explicitly selecting columns.
`untaped plugins add` and `untaped plugins remove` accept package specs from
multiple positionals or `--stdin`; when syncing, each command mutates the full
batch first and exact-syncs the managed untaped virtual environment once.
Core and plugins are installed into
`${XDG_DATA_HOME:-~/.local/share}/untaped/venv`, with `~/.local/bin/untaped`
as the user-facing shim. Synced plugin commands require the core install spec
recorded by the managed installer; do not fall back to an implicit core spec.
Keep long-running `uv` resolution/install work outside the config file lock and
serialize managed venv writes with the plugin environment lock. Do not
reintroduce `uv tool install --with` as the plugin lifecycle; plugin
dependencies belong in normal package metadata and `uv pip sync` owns
resolution. Managed sync uses the recorded core/plugin specs as the runtime
source of truth and invokes `uv pip compile --no-sources`, so plugin
repo-local `[tool.uv.sources]` tables remain development metadata only.
Dependencies that exist only in a plugin checkout's `[tool.uv.sources]` table
are not installed by managed sync. For editable multi-plugin development,
record every local plugin checkout that should be installed into the managed
environment.

Profile selection has two distinct CLI meanings. Read-time overrides use
`ProfileOverrideOption` plus `profile_override(...)` and expose `--profile`
on the command that reads settings; the helper restores `UNTAPED_PROFILE`
and clears the `get_settings()` cache after the command body. Config mutation
commands use `--target-profile` when choosing which profile to write to.

The default view is one row per plugin package/name, not separate rows for
"loaded" and "desired" state. A desired package such as `untaped-awx` is
coalesced with a loaded plugin id such as `awx` when the normalized suffix
matches; the row status is `installed`, `recorded`, or `loaded`.

## Cross-Cutting helpers (`untaped`)

| Need                                       | Use                                                              |
| ------------------------------------------ | ---------------------------------------------------------------- |
| Read typed plugin config                   | `from untaped import get_config_section`                    |
| Resolve settings once per command (profile-aware, no global state) | `from untaped.api import plugin_context`; read sections with `ctx.section(name, Model)` |
| Build a validated plugin HTTP client (token check + bearer auth + TLS) | `from untaped.api import connected_client` |
| Standard "setting not configured" error    | `from untaped.api import missing_setting_error`             |
| Walk paginated API collections             | `from untaped.api import paginate_offset, paginate_pages`   |
| Read core settings only                    | `from untaped import get_core_settings`                     |
| Add a command-local read-time profile override | `from untaped import ProfileOverrideOption, profile_override` (prefer `plugin_context(profile)` in new code) |
| Resolve TLS verify (OS trust + ca_bundle)  | `from untaped import resolve_verify`                        |
| Make an HTTP call                          | `from untaped import HttpClient`                            |
| Render semantic output/messages with active theme | `from untaped import ui_context, UiContext, ThemeSpec` |
| Prompt for typed interactive input         | `from untaped import ui_context, PromptChoice`; use `ui_context(strict=False).confirm/text/secret/select/multiselect(...)` |
| Register an agent skill from a plugin | `from untaped.plugins import SkillSpec`; call `registry.add_skill(...)` |
| Format row output without reading config (compatibility wrapper) | `from untaped import format_output, OutputFormat` |
| Add `--format` / `--columns` to a Cyclopts command | `from untaped import FormatOption, ColumnsOption`      |
| Render `--format`/`--columns` row collections | `from untaped import render_rows` (themed table for humans; theme-independent json/raw for pipes) |
| Reject bad usage with `error: ...` + exit 2 | `from untaped import raise_usage`                          |
| Wrap a command body so `UntapedError` → exit 1 | `from untaped import report_errors`                     |
| Read piped values from stdin               | `from untaped import read_stdin`                            |
| Resolve identifiers from positionals or stdin (one source only) | `from untaped import read_identifiers` |
| Loop over identifiers with per-id `error: <id>: <exc>` rows | `from untaped import resolve_each`         |
| Parse repeated `KEY=VALUE` flags           | `from untaped import parse_kv_pairs`                        |
| Clamp `--parallel N` at an upper bound with a stderr warning | `from untaped import clamp_parallel` (caller supplies `cap` and `policy`) |
| Print a semantic status/warning/info message to stderr | `ui_context(strict=False).message(kind, msg)` |
| Prompt users interactively                 | `ui_context(strict=False).confirm/text/secret/select/multiselect(...)`; prompts require TTY stdin and render on stderr |
| Print raw logs, command passthrough, or low-level fallback errors | `echo(msg, err=True)` |
| Inject a stderr-warning hook into a use case | accept `warn: Callable[[str], None]` in `__init__`; `cli/` wires `echo(f"warning: {msg}", err=True)` |
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
| Declare port `Protocol`s for a plugin      | `<plugin>/src/untaped_<x>/application/ports.py` (Hard Rule #10) |
| Declare DTOs that cross app/infra boundary | `<plugin>/src/untaped_<x>/domain/payloads.py` (pydantic `BaseModel` with `frozen=True`) |

Cross-cutting subsystems with their own internals doc:

- **Configuration, profiles, plugin installs, and TLS** live in `src/untaped/`.
  User-facing reference: [`docs/configuration.md`](docs/configuration.md).
  `untaped config get/set/unset` manages scalar settings. Profile keys resolve
  through the active or requested profile; scalar `ui.*` keys are the deliberate
  exception because they read/write the top-level global `ui:` block.
  Structured app state such as `plugins.*`, `workspace.*`, `ui.symbols`, and
  `ui.color_roles` stays outside `config get/set/unset`.
- **Workspace management** lives in the extracted
  [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
  plugin. Core plugin install guidance lives in
  [`docs/plugins.md`](docs/plugins.md); command reference lives in the plugin
  repo.
- **AWX resource framework, apply pipeline, jobs/track, test runner** live
  in the extracted
  [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx)
  plugin. Core plugin install guidance lives in
  [`docs/plugins.md`](docs/plugins.md); command reference lives in the plugin
  repo.
- **GitHub authenticated user and search** live in the extracted
  [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github)
  plugin. Core plugin install guidance lives in
  [`docs/plugins.md`](docs/plugins.md); command reference lives in the plugin
  repo.

## Conventions

- **Module docstrings.** Every source module (`*.py`) opens with a
  module docstring describing what it owns. Re-export stubs (layer
  `__init__.py` files like `cli/__init__.py`,
  `infrastructure/__init__.py`, …) are exempt — they're plumbing,
  with nothing to describe.
- **Re-export the public surface.**
  - **Plugin packages**: `<pkg>/__init__.py` re-exports `app:
    cyclopts.App` *lazily* via a module-level `__getattr__` (PEP 562) —
    an eager `from untaped_<x>.cli import app` would defeat the
    manifest's `import_path` laziness, because loading
    `untaped_<x>.plugin` from the entry point imports the package
    `__init__` first. `<pkg>/plugin.py` exposes the entry-point object
    that declares the manifest. Public adapters live in
    `infrastructure/__init__.py` with explicit `__all__`.
  - **`untaped`**: re-exports its public plugin/core API from
    `src/untaped/__init__.py` with explicit `__all__`.
- **Per-command flags vs shared option types.** Per-command flags in
  `cli/commands.py` use `Annotated[..., Parameter(...)]` at the call
  site. Shared option types reused across commands live in `untaped` as
  `Annotated[…, Parameter(…)]` aliases (e.g. `FormatOption`,
  `ColumnsOption`).
- **`errors.py` placement.** Domain packages with their own exception
  subclasses keep them in a top-level `errors.py`; `untaped-awx`
  additionally has `infrastructure/errors.py` for HTTP-status →
  exception mapping. Plugins that only raise `untaped` exceptions don't
  need an `errors.py`.
- **Lazy imports on CLI startup paths.** Heavy transitive imports that
  would pay on every `untaped --help` are deferred into subcommand
  bodies. Add `# noqa: PLC0415` only when Ruff currently flags that
  specific import. Scope: any module reached on the import graph from
  `src/untaped/main.py` at startup. Enforced by ruff
  (`extend-select = ["PLC0415"]`); tests are exempted.

## Output & Piping Conventions

- **stdout = data only.** Never print logs, prompts, or progress to
  stdout.
- **stderr = everything else.** Logs, progress, prompts. Use
  `echo(msg, err=True)`.
- **Row-oriented data commands** (`list`, `status`, row-producing `get`,
  …) expose:
  - `--format / -f` (`json | yaml | table | raw`); default `table` for
    `list`, `yaml` for `get`.
  - `--columns / -c` (repeatable). Dotted paths supported
    (`summary_fields.project.name`).
  - `--stdin` to consume newline-separated identifiers when the command
    takes a list.
- **Scalar detail commands** may deliberately omit `--columns` and choose a
  command-specific format default. `untaped config get <key>` defaults to raw
  value output so it can be used directly in shell scripts.
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
uv sync                                         # install / sync core
uv run pre-commit install                       # local lint at commit + mypy at push
uv add --group dev some-test-helper             # dev dep on the root
uv run pytest                                   # tests with coverage (gate: 80%)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
uv run untaped --help                           # run the CLI from source
scripts/install.sh --editable .                 # install the managed `untaped` shim
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
1. Write the failing test (`tests/unit/` for core; plugin repos use
   their own `tests/unit/`).
2. Run it; confirm it fails for the right reason.
3. Implement the smallest change that makes it pass.
4. Refactor with the test still green.

**Test layout:**
- Core tests live in `tests/unit/`. Plugin tests live in each plugin
  repo. Use `tests/integration/` when the test exercises a real
  subprocess or fake-server fixture.
- No `__init__.py` files inside `tests/` — pytest uses
  `--import-mode=importlib`.
- Mock httpx with `respx` (already a dev dep).
- For CLI tests, use `untaped.testing.CliInvoker`.

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

## Recipe: Add a new plugin repo

```bash
# 1. Create the plugin repo/package
uv init --package --lib untaped-<X>
# 2. Add deps
uv add 'cyclopts>=4.16.0,<5' 'untaped>=0.2.0'
# 3. Build the 4 layers
mkdir -p src/untaped_<x>/{cli,application,domain,infrastructure}
mkdir -p tests/unit
```

Then:
- Implement `domain/models.py`, `infrastructure/<x>_client.py`,
  `application/<use_case>.py`, `cli/commands.py`.
- Re-export `app` from `cli/__init__.py` and the package `__init__.py`.
- Add `plugin.py` and declare the manifest (do not import the CLI module
  here — the `import_path` keeps it off the startup path):
  ```python
  from untaped.api import CliSpec, PluginManifest

  class XPlugin:
      id = "<x>"
      untaped_api_version = 3

      def manifest(self) -> PluginManifest:
          return PluginManifest(
              clis=(CliSpec(name="<x>", import_path="untaped_<x>.cli:app", help="..."),),
          )

  plugin = XPlugin()
  ```
- Add the entry point to the package `pyproject.toml`:
  ```toml
  [project.entry-points."untaped.plugins"]
  <x> = "untaped_<x>.plugin:plugin"
  ```
- Add a package-local `AGENTS.md` for domain-specific internals
  (resource framework, side-effect adapters, polling cadence, …) plus a
  short `CLAUDE.md` stub pointing back to that file and the core repo.
- Add plugin docs with the package-specific install spec and command usage.
  Link to `untaped` core's `docs/plugins.md` for generic direct install,
  managed state, editable source, and multi-plugin sync workflows; do not
  duplicate those full flows in every plugin repo.
- Run `uv sync && uv run pytest && uv run untaped <x> --help`.

## Recipe: Add a new command to an existing plugin

1. **Test first** in the plugin repo's `tests/unit/test_<feature>.py`.
2. New external call → add a method to the existing
   `infrastructure/<x>_client.py`. Don't create a new client class
   unless it's a different service.
3. New domain logic → add an entity/method in `domain/`, a use case in
   `application/`.
4. Add the Cyclopts command in `cli/commands.py`:
   - use `@app.command(name="documented-name")` for public commands
   - express options/arguments as `Annotated[..., Parameter(...)]`
   - required inputs are required positional-only params
     (`Parameter(help=...)` before `/`); missing values become
     `error: ... requires an argument` (exit 2) automatically
   - stdin-fed list commands guard with
     `raise_usage("provide <thing>(s) or --stdin")` when no input source
     is given
   - suppress the auto `--no-*` variant on action-like boolean flags with
     `negative=""` (`--yes`, `--stdin`, `--clear-*`); keep it for genuine
     persistent toggles
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
- **Adding a new dep with `pyproject.toml` edits.** Use `uv add` in the
  repo that owns the dependency.
- **Writing a helper inside a plugin that another plugin will need.**
  Move it to `src/untaped/` only if it is hub plumbing; otherwise use a
  separate shared lib (move ≠ copy).
- **Forgetting the plugin entry point.** Test: does
  `uv run untaped --help` list the plugin command after the package is
  installed in the environment?
- **Relying on inferred Cyclopts public names.** Public command and option
  paths should use explicit `name=...` metadata so documented CLI contracts
  do not drift when Python identifiers change. Positional-only parameters
  are the exception: they take `Parameter(help=...)` with no `name=` —
  `Parameter(name="")` renders broken help and error text.
- **Using an `Annotated[..., Parameter(...)]` alias as a default value.**
  Put shared aliases in the annotation position (`value: Alias = default`);
  otherwise Cyclopts/Pydantic may validate the alias object itself.
- **Adding a new setting without thinking about secrets and TLS.**
  Credentials → `pydantic.SecretStr`. Hostnames for TLS services → the
  client must use `resolve_verify(get_core_settings().http)`.
- **Naming a method `list` on a class whose annotations elsewhere include
  `list[X]`.** mypy resolves `list` to the method, not the builtin. Use
  `entries` instead, or `Iterator[X]` / `Iterable[X]` returns (no
  collision).
- **Changing workspace git semantics in core.** Workspace git operations
  live in the extracted `untaped-workspace` plugin; update that repo's
  `AGENTS.md` and tests with any workspace behavior change.

## See also

- **Plugins**:
  [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx),
  [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible),
  [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github),
  [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira),
  [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile),
  [`untaped-themes`](https://github.com/alexisbeaulieu97/untaped-themes),
  [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
- **User-facing docs**: [`docs/`](docs/README.md) — core configuration and
  plugin install/sync UX. Plugin command references live in the plugin repos.
