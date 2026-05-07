# AGENTS.md — `untaped`

Single source of truth for how `untaped` is built. AI agents and humans
both read this file; per-package internals live in
`packages/<x>/AGENTS.md`. If you change architecture, update the relevant
file in the same commit.

## Mission

`untaped` is a personal DevOps CLI suite. Each domain (workspace, awx,
github, …) is its own Python package exposing a Typer sub-app; one binary
(`untaped`) aggregates them. Daily DevOps work composes — data-emitting
commands are pipe-friendly. We build *on top of* existing CLIs (`gh`,
`awx-cli`), never reimplement them.

## Repository Map

The workspace root **is** the `untaped` package — it owns the binary and
aggregates every domain. Each domain lives as a workspace member under
`packages/`.

```
untaped/
├── pyproject.toml                # workspace root + the `untaped` package
├── uv.lock                       # single shared lockfile (commit it)
├── .python-version               # 3.14
├── AGENTS.md                     # ← you are here (root rules)
├── CLAUDE.md                     # imports @AGENTS.md
├── README.md                     # human-facing intro
├── docs/                         # user-facing reference
├── src/untaped/                  # root CLI: aggregates domains via add_typer
├── tests/                        # tests for the root CLI
└── packages/
    ├── untaped-core/             # shared infra; AGENTS.md
    ├── untaped-config/           # `config` meta-domain
    ├── untaped-profile/          # `profile` meta-domain
    ├── untaped-workspace/        # local git workspaces; AGENTS.md
    ├── untaped-awx/              # AWX/AAP API; AGENTS.md
    └── untaped-github/           # GitHub authenticated user
```

| Package             | Type | Owns                                                                  | Internals doc |
| ------------------- | ---- | --------------------------------------------------------------------- | ------------- |
| `untaped` (root)    | app  | The `untaped` binary; aggregates domain sub-apps. Hosts `--profile`.  | this file     |
| `untaped-core`      | lib  | Settings, http+TLS, config schema/file, profiles, output, stdin.      | [`packages/untaped-core/AGENTS.md`](packages/untaped-core/AGENTS.md) |
| `untaped-config`    | lib  | The `config` meta-domain (operates on profile contents).              | —             |
| `untaped-profile`   | lib  | The `profile` meta-domain (manages the profile inventory).            | —             |
| `untaped-workspace` | lib  | Per-workspace `untaped.yml` manifests + central registry; subprocess `git`. | [`packages/untaped-workspace/AGENTS.md`](packages/untaped-workspace/AGENTS.md) |
| `untaped-awx`       | lib  | AWX/AAP bounded context (jobs, templates, inventories, …).            | [`packages/untaped-awx/AGENTS.md`](packages/untaped-awx/AGENTS.md) |
| `untaped-github`    | lib  | GitHub bounded context — authenticated user only today.               | —             |

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
3. **Register every new domain in `src/untaped/main.py`.** A domain not
   wired into the root CLI does not exist as far as users are concerned.
4. **Use `uv init --package` with `--lib` or `--app`.** Never the bare
   `uv init` (gives flat layout).
5. **Create a lib if reuse is likely.** Don't duplicate code across
   domains. If two domains need the same helper, it belongs in
   `untaped-core`.
6. **Search before writing.** Grep `untaped-core` and other packages
   before implementing a helper. If it exists in the wrong place, *move*
   it (and update callers); don't fork.
7. **Finish each session with `uv run ruff check --fix && uv run ruff
   format`.** No exceptions.
8. **Write tests that verify the result.** TDD: failing test first, then
   implementation. Test through public APIs — never suppress warnings to
   access private members.
9. **Every Typer app and every command with required args sets
   `no_args_is_help=True`.** No-args invocation must show help, not error.
10. **Mark every secret as `pydantic.SecretStr`.** Tokens, passwords, API
    keys. `untaped config list` redacts them; `repr(settings)` won't leak
    them in tracebacks. Call `.get_secret_value()` only at point of use.
11. **Use `resolve_verify(settings.http)` for every httpx client.** Never
    hard-code `verify=True/False` or a path.
12. **Use absolute imports across the workspace.** `from untaped_<x>.… import …`
    or `from untaped_core import …`, never `from .foo import bar`.
    Enforced by ruff's `ban-relative-imports = "all"`; applies inside
    every package, including tests.

## Architecture: 4-Layer DDD per domain

Every domain package (`untaped-<X>`) has the same internal layout:

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
- `application/` orchestrates: defines `Protocol`s for what it needs, takes
  them via constructor (DI).
- `infrastructure/` knows about httpx, the filesystem, the config file.
- `cli/` is the thinnest layer: parse Typer args, build a use case
  (passing in concrete adapters), call it, format the result.

A use case in `application/` is unit-testable with a stub satisfying its
`Protocol` — no httpx, no fixtures, no settings file.

## Cross-Cutting helpers (`untaped-core`)

| Need                                       | Use                                                              |
| ------------------------------------------ | ---------------------------------------------------------------- |
| Read user configuration                    | `from untaped_core import get_settings`                          |
| Resolve TLS verify (OS trust + ca_bundle)  | `from untaped_core import resolve_verify`                        |
| Make an HTTP call                          | `from untaped_core import HttpClient`                            |
| Format output for stdout                   | `from untaped_core import format_output, OutputFormat`           |
| Add `--format` / `--columns` to a Typer command | `from untaped_core import FormatOption, ColumnsOption`      |
| Wrap a command body so `UntapedError` → exit 1 | `from untaped_core import report_errors`                     |
| Read piped values from stdin               | `from untaped_core import read_stdin`                            |
| Resolve identifiers from positionals or stdin (one source only) | `from untaped_core import read_identifiers` |
| Parse repeated `KEY=VALUE` flags           | `from untaped_core import parse_kv_pairs`                        |
| Print a one-line message to stderr         | `typer.echo(msg, err=True)` — keep it boring; no helper          |
| Inject a stderr-warning hook into a use case | accept `warn: Callable[[str], None]` in `__init__`; `cli/` wires `typer.echo(f"warning: {msg}", err=True)` |
| Raise a typed error                        | subclass `untaped_core.UntapedError`                             |
| Walk the Settings schema (for tooling)     | `from untaped_core.config_schema import walk_settings`           |
| Read/write `~/.untaped/config.yml`         | `from untaped_core.config_file import read_config_dict, write_config_dict, set_at_path, unset_at_path` |
| Atomic read-modify-write the config file   | `from untaped_core.config_file import mutate_config` (file-locked) |
| Read/write a single profile                | `from untaped_core.config_file import read_profile, write_profile, list_profile_names, get_active_profile_name, set_active_profile, delete_profile, rename_profile` |
| Merge `default` ⤥ active to an effective dict | `from untaped_core import resolve_profiles` |
| Mark a secret field                        | `pydantic.SecretStr`                                             |

Cross-cutting subsystems with their own internals doc:

- **Profiles** and **TLS** — see
  [`packages/untaped-core/AGENTS.md`](packages/untaped-core/AGENTS.md).
  User-facing reference: [`docs/configuration.md`](docs/configuration.md).
- **Workspace internals** — see
  [`packages/untaped-workspace/AGENTS.md`](packages/untaped-workspace/AGENTS.md).
  User-facing reference: [`docs/workspace.md`](docs/workspace.md).
- **AWX resource framework, apply pipeline, jobs/track, test runner** —
  see [`packages/untaped-awx/AGENTS.md`](packages/untaped-awx/AGENTS.md).
  User-facing reference: [`docs/awx.md`](docs/awx.md).

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

Pipeline examples and the morning-routine workflow live in
[`docs/README.md`](docs/README.md#pipe-friendly-by-design).

## Development Workflow

```bash
uv sync --all-packages                          # install / sync everything
uv add --package untaped-awx httpx-retries      # runtime dep on a package
uv add --group dev some-test-helper             # dev dep on the root
uv run pytest                                   # tests with coverage (gate: 80%)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
uv run untaped --help                           # run the CLI from source
uv tool install --editable .                    # install the `untaped` binary globally
```

User-facing config / profile commands are documented in
[`docs/configuration.md`](docs/configuration.md).

**TDD loop:**
1. Write the failing test (in `packages/untaped-<x>/tests/unit/`).
2. Run it; confirm it fails for the right reason.
3. Implement the smallest change that makes it pass.
4. Refactor with the test still green.

**Test layout:**
- Tests live in `packages/<pkg>/tests/unit/`.
- No `__init__.py` files inside `tests/` — pytest uses
  `--import-mode=importlib`.
- Mock httpx with `respx` (already a dev dep).
- For CLI tests, use `typer.testing.CliRunner`.

## Decision Tree: Where does this code go?

1. **Shared across two or more domains?** → `untaped-core/` (or a new
   shared lib if it's a coherent subdomain).
2. **CLI-only (argument parsing, output formatting)?** → `cli/` inside
   the domain.
3. **Pure business logic — entities, value objects, invariants?** →
   `domain/`.
4. **Talks to an external service (HTTP, fs, subprocess)?** →
   `infrastructure/`.
5. **Orchestrates steps between domain and infrastructure?** →
   `application/`.

## Recipe: Add a new domain package

```bash
# 1. Create the package
uv init --package --lib packages/untaped-<X>
# 2. Add deps
uv add --package untaped-<X> typer untaped-core
# 3. Build the 4 layers
mkdir -p packages/untaped-<X>/src/untaped_<x>/{cli,application,domain,infrastructure}
mkdir -p packages/untaped-<X>/tests/unit
```

Then:
- Implement `domain/models.py`, `infrastructure/<x>_client.py`,
  `application/<use_case>.py`, `cli/commands.py`. Add `@app.callback()`
  (single-command Typer apps collapse without it).
- Re-export `app` from `cli/__init__.py` and the package `__init__.py`.
- **Register in `src/untaped/main.py`:**
  ```python
  from untaped_<x> import app as <x>_app
  app.add_typer(<x>_app, name="<x>")
  ```
- `uv add untaped-<X>` to add the package as a runtime dep of the root.
- Add the module to `[tool.mypy] packages = [...]` in the root
  `pyproject.toml`.
- Add the package to this file's Repository Map.
- **Create `packages/untaped-<X>/AGENTS.md`** for domain-specific
  internals (resource framework, side-effect adapters, polling cadence,
  …) plus a 4-line `CLAUDE.md` stub: `See @AGENTS.md for <pkg>
  internals. For workspace-wide rules see @../../AGENTS.md.`
- Run `uv sync && uv run pytest && uv run untaped --help`. If you have
  the global tool installed, re-run `uv tool install --editable .`.

## Recipe: Add a new command to an existing domain

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

The schema lives in `untaped-core`; the recipe lives there too. See
[`packages/untaped-core/AGENTS.md`](packages/untaped-core/AGENTS.md#recipe-add-a-new-setting).

## Common Mistakes

- **Importing httpx, pyyaml, or os in a `domain/` module.** Domain is
  pure. Move the call to `infrastructure/`.
- **A `cli/` module calling business logic from `infrastructure/`
  instead of going through an `application/` use case.** Wiring concrete
  adapters at the composition root (e.g. `cli/_context.py`) is fine; the
  ban is on bypassing application use cases for the actual logic.
- **Adding a new dep with `pyproject.toml` edits.** Use `uv add --package`.
- **Writing a helper inside a domain that another domain will need.**
  Move it to `untaped-core` immediately (move ≠ copy).
- **Forgetting to register a new domain in `src/untaped/main.py`.**
  Test: does `uv run untaped --help` list it?
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
- **Adding a new git operation outside `GitRunner`.** All git subprocess
  calls live in
  `untaped_workspace.infrastructure.git_runner.GitRunner`.
- **Auto-switching branches on `sync`.** Don't. Branch cascade is
  clone-time only; diverged on-disk vs target → skip-with-warning, never
  `git checkout` for the user.

## See also

- **Per-package internals**:
  [`untaped-core`](packages/untaped-core/AGENTS.md),
  [`untaped-workspace`](packages/untaped-workspace/AGENTS.md),
  [`untaped-awx`](packages/untaped-awx/AGENTS.md)
- **User-facing docs**: [`docs/`](docs/README.md) — configuration,
  workspaces, AWX, GitHub
