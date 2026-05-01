# AGENTS.md — `untaped`

This file is the **single source of truth** for how `untaped` is built. AI
agents and humans both read it. Keep it up to date whenever the project's
structure, tooling, or conventions change.

If you change the architecture, change this file in the same commit.

## Mission

`untaped` is a personal DevOps CLI suite that grows over time. Each domain
(workspace, awx, github, …) is its own Python package, exposing a Typer
sub-app. A single binary, `untaped`, aggregates all sub-apps so the user
sees one tool.

**Goals**
- Make daily DevOps work composable: every command produces stdout suitable
  for piping into `fzf`, `jq`, `awk`, `cut`, or another `untaped` command.
- Stay easy to extend: adding a new domain is a recipe (see below), not a
  project.
- Catch errors at the boundary, not in the middle: validate config and API
  payloads with Pydantic, raise typed exceptions.

**Non-goals**
- Reimplementing existing CLIs (e.g. `awx-cli`, `gh`). We build *on top of*
  their APIs to fit our own workflows.

## Repository Map

The workspace root **is** the `untaped` package — it owns the binary and
aggregates every domain. Each domain lives as its own workspace member
under `packages/`.

```
untaped/
├── pyproject.toml                # workspace root + the `untaped` package
├── uv.lock                       # single shared lockfile (commit it)
├── .python-version               # 3.14
├── .pre-commit-config.yaml
├── AGENTS.md                     # ← you are here
├── CLAUDE.md                     # imports @AGENTS.md
├── README.md                     # human-facing intro
├── src/
│   └── untaped/                  # root CLI: `untaped --help`; `add_typer` per domain
│       ├── __init__.py
│       ├── __main__.py
│       └── main.py
├── tests/                        # tests for the root CLI
└── packages/
    ├── untaped-core/             # shared infra (settings, http, config, profiles, output, stdin, logging, errors)
    ├── untaped-config/           # `untaped config list/set/unset` (operates on profiles)
    ├── untaped-profile/          # `untaped profile list/show/use/create/delete/rename`
    ├── untaped-workspace/        # manage local git workspaces
    ├── untaped-awx/              # Ansible Automation Platform / AWX API
    └── untaped-github/           # GitHub API search & inspection
```

| Package             | Type | Owns                                                                  |
| ------------------- | ---- | --------------------------------------------------------------------- |
| `untaped` (root)    | app  | The `untaped` binary; aggregates domain sub-apps via `add_typer`. Hosts the root `--profile` flag. |
| `untaped-core`      | lib  | Cross-cutting: settings, http (incl. TLS), config schema/file, profiles (resolver + helpers), logging, output, stdin. |
| `untaped-config`    | lib  | The `config` meta-domain: introspect/edit profile contents in `~/.untaped/config.yml`. |
| `untaped-profile`   | lib  | The `profile` meta-domain: list/show/use/create/delete/rename profiles. |
| `untaped-workspace` | lib  | Workspace bounded context: per-workspace `untaped.yml` manifests, central `name → path` registry, sync/status/foreach via subprocess `git`. |
| `untaped-awx`       | lib  | AWX/AAP bounded context (jobs, templates, inventories, …).            |
| `untaped-github`    | lib  | GitHub bounded context (search, repos, users, …).                     |

## Hard Rules

These are non-negotiable. Every contribution must respect them.

1. **Always keep this file (`AGENTS.md`) up to date.** No drift. If you add a
   package, change the DDD layout, or add a cross-cutting helper, edit this
   file in the same commit.
2. **Prefer `uv` commands over manual `pyproject.toml` edits.** Use
   `uv add`, `uv add --package <name>`, `uv add --group dev`,
   `uv init --package --lib|--app`. Hand-editing `pyproject.toml` is fine for
   tool config (`[tool.ruff]`, `[tool.mypy]`, …) but never for dependencies.
3. **Register every new domain in `src/untaped/main.py`.** A domain not
   wired into the root CLI does not exist as far as users are concerned.
4. **Use `uv init --package` with `--lib` or `--app`** depending on whether
   you're building a shared library (`--lib`) or the CLI binary (`--app`).
   Never use the bare `uv init` (gives flat layout).
5. **Create a lib if reuse is likely.** Don't duplicate code across
   domains. If two domains need the same helper, it belongs in
   `untaped-core` (or a new shared lib if it's a coherent subdomain).
6. **Search before writing.** Before implementing a helper, grep
   `untaped-core` and other packages. If it exists in the wrong place,
   *move* it (and update callers); don't fork.
7. **Finish each session with `uv run ruff check --fix && uv run ruff
   format`.** No exceptions.
8. **Write tests that verify the result.** TDD: write the failing test
   first, then the implementation. Test through public APIs — never
   suppress warnings to access private members.
9. **Every Typer app and every command with required args sets
   `no_args_is_help=True`.** Running a command with no args must show its
   help, not error with "missing argument". Set it on the `Typer(...)`
   constructor for sub-apps, and on `@app.command(..., no_args_is_help=True)`
   for individual commands that take required positional/option args.
10. **Mark every secret as `pydantic.SecretStr`.** Tokens, passwords, API
    keys. The `untaped config list` table redacts them automatically;
    `repr(settings)` won't leak them in tracebacks. Call
    `.get_secret_value()` only at the point of use (e.g. building an
    `Authorization` header).
11. **Use `resolve_verify(settings.http)` for every httpx client.** Never
    hard-code `verify=True/False` or a path. The helper picks the OS trust
    store, an explicit `ca_bundle`, or disabled verification based on the
    user's settings.

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

Rules:
- `domain/` imports nothing from the other three layers — it's pure.
- `application/` orchestrates: it defines `Protocol`s for what it needs and
  takes them in via the constructor (dependency injection).
- `infrastructure/` knows about httpx, the filesystem, the config file.
- `cli/` is the thinnest layer: parse Typer args, build an application use
  case (passing in concrete infrastructure adapters), call it, format the
  result.

This means a use case in `application/` can be unit-tested with a stub
that satisfies the `Protocol` — no httpx, no fixtures, no settings file.

## Cross-Cutting helpers (`untaped-core`)

| Need                                       | Use                                                              |
| ------------------------------------------ | ---------------------------------------------------------------- |
| Read user configuration                    | `from untaped_core import get_settings`                          |
| Resolve TLS verify (OS trust + ca_bundle)  | `from untaped_core import resolve_verify`                        |
| Make an HTTP call                          | `from untaped_core import HttpClient`                            |
| Format output for stdout                   | `from untaped_core import format_output, OutputFormat`           |
| Read piped values from stdin               | `from untaped_core import read_stdin`                            |
| Log to stderr                              | `from untaped_core import get_logger`                            |
| Raise a typed error                        | subclass `untaped_core.UntapedError`                             |
| Walk the Settings schema (for tooling)     | `from untaped_core.config_schema import walk_settings`           |
| Read/write `~/.untaped/config.yml`         | `from untaped_core.config_file import read_config_dict, write_config_dict, set_at_path, unset_at_path` |
| Read/write a single profile                | `from untaped_core.config_file import read_profile, write_profile, list_profile_names, get_active_profile_name, set_active_profile, delete_profile` |
| Merge `default` ⤥ active to an effective dict | `from untaped_core.profile_resolver import resolve_profiles` |
| Mark a secret field                        | `pydantic.SecretStr` (auto-redacted by `untaped config list`)    |

If you find yourself writing one of these inside a domain, stop — pull the
helper from `untaped-core` instead, or add it there if it's missing.

### Profiles

`~/.untaped/config.yml` is **profile-based**: every configurable value
lives under `profiles.<name>`, never at the top level. Two profile-related
keys live outside that block:

- `active: <name>` — selects which profile is active. May be unset (then
  `default` is used). The `UNTAPED_PROFILE` env var or the root
  `untaped --profile <name>` flag override this for one process.
- `workspace.workspaces` — the workspace registry. This is **app state**,
  not user-tunable config, so it stays at the top level and is hoisted
  back into the merged dict by `ProfilesSettingsSource`.

Resolution order, high → low:

```
env vars (UNTAPED_…)  >  active profile  >  default profile  >  schema default
```

Two-layer fallback only — `prod` ⤥ `default` ⤥ schema. There is no
profile-to-profile inheritance beyond the implicit `default` fallback.

The `default` profile is required (the resolver raises if `profiles.*` is
non-empty without it). It is also the bottom layer for everything: any
named profile can declare just the keys that differ.

`untaped config set <key> <value>` writes to the active profile by default;
`--profile <name>` targets a different one (the named profile must already
exist; `default` is auto-bootstrapped). `untaped profile <…>` manages the
profile inventory itself: `list`, `show`, `use`, `create [--copy-from]`,
`delete`, `rename`. Deleting `default` or the active profile is refused.

The root `--profile` flag mutates `os.environ["UNTAPED_PROFILE"]` and
clears `get_settings`'s `lru_cache` immediately, so per-call overrides
take effect even when the cache was already populated.

### TLS verification

`untaped-core` defaults to the **OS trust store** via the `truststore`
package. Corporate root CAs that the user has installed in macOS Keychain,
Windows certstore, or the Linux system trust will "just work" without any
configuration.

Override paths:
- `untaped config set http.ca_bundle /path/to/corp-ca.pem` — explicit bundle.
- `untaped config set http.verify_ssl false` — escape hatch (warning-worthy).

Implementation: every domain client passes `verify=resolve_verify(s.http)`
when constructing `HttpClient`. Do not invent your own.

### `untaped-workspace` — manifest + registry split

A workspace has two homes:

- **Manifest** (per-workspace, source of truth): `<workspace-dir>/untaped.yml`
  declares `name`, `defaults` (`branch`), and `repos` (list of `{url, name?, branch?}`).
  Read/written by `infrastructure.ManifestRepository`.
- **Registry** (central): a `name → path` list under `workspace.workspaces` in
  `~/.untaped/config.yml`. Just enough to power `list`, `path <name>`,
  `--name X` lookups, and tab completion. Read/written by
  `infrastructure.WorkspaceRegistryRepository` (method names: `entries`, `get`,
  `find_by_path`, `register`, `unregister` — *not* `list`, which would shadow
  the `list` builtin in nested annotations within the class).

Workspace lookup (every command except `list`/`path`/`shell-init`/`edit`):
explicit `--name` → registry lookup; explicit `--path` → manifest lookup;
otherwise walk up from cwd looking for `untaped.yml`. Implemented in
`infrastructure.WorkspaceResolver`.

Git is a **subprocess** dependency (`infrastructure.GitRunner`), not a
library. Bare clones are cached in `workspace.cache_dir`
(default `~/.untaped/repositories`); workspace clones use
`git clone --reference` against the bare so disk + bandwidth are shared
without `git worktree` branch conflicts.

Branch cascade is **clone-time only**: per-repo `branch` > workspace
`defaults.branch` > the remote's HEAD. Subsequent `sync`s do not
auto-switch branches — they skip-with-warning when the on-disk branch
doesn't match the manifest's target. This stops a stale `defaults.branch`
from kidnapping a user mid-`feature/x`. See
`application.SyncWorkspace._sync_repo` for the state machine.

### `untaped-awx` — declarative resource framework

The AWX bounded context follows the same DDD layout but builds a small
**resource framework** because the surface (5+ kinds × list/get/save/apply
+ launch) is too uniform to hand-write per-kind without copy-paste.

- **`ResourceSpec`** (in `domain/spec.py`) declares each kind's
  contract: `kind`, `cli_name`, `api_path`, `identity_keys`,
  `canonical_fields`, `read_only_fields`, `fk_refs`, `secret_paths`,
  `actions`, `apply_strategy`, `commands`, `fidelity`, `list_columns`,
  `list_filters`. Per-kind specs live in
  `infrastructure/specs/{job_template,workflow,project,credential,
  schedule,_support}.py` and are aggregated into `ALL_SPECS`.
- **kubectl-style envelope** for saved files (`domain/envelope.py`):
  `{kind, apiVersion, metadata: {name, organization, parent?}, spec}`.
  FK references are by name (scoped to `metadata.organization`); the
  polymorphic Schedule parent is a typed `IdentityRef`.
- **Apply is preview-by-default** (`application/apply_resource.py`).
  Writes require `--yes`. The diff is field-level; declared
  `secret_paths` (e.g. `inputs.*`, `webhook_key`) carrying
  `$encrypted$` are stripped from PATCH and shown as
  `(preserved existing secret)` rows. `$encrypted$` at *undeclared*
  paths fires a stderr warning and is dropped (paranoid net).
- **`ApplyStrategy`** is a Protocol in `application/ports.py`. The
  default strategy uses plain CRUD; `ScheduleApplyStrategy` POSTs
  against `<parent_path>/<parent_id>/schedules/` for create and
  PATCHes the global `/schedules/<id>/` for update. Each spec names
  its strategy; `infrastructure/strategy_resolver.py` injects the
  concrete instance.
- **`Catalog`** is also a Protocol; `infrastructure/catalog.py`
  provides the static `AwxResourceCatalog` over `ALL_SPECS`. Use
  cases never import infrastructure — CLI wires concrete adapters at
  the composition root (`cli/_context.py`).
- **AAP/AWX compatibility** via `awx.api_prefix` (default
  `/api/controller/v2/` for AAP; users on upstream AWX set
  `/api/v2/`). Every URL flows through `AwxClient._url(path)` so the
  prefix is honoured uniformly.
- **Restore fidelity tiers**: `full` (JT, Project, Schedule), `partial`
  (WorkflowJobTemplate header — node graph deferred to v0.5),
  `read_only` (Credential — `$encrypted$` roundtrip deferred,
  Organization, Inventory, CredentialType — CRUD deferred). Saves
  below `full` echo the tier to stderr and embed an inline YAML
  comment.
- **Apply ordering** for multi-doc files / directories: hardcoded
  dependency order in `application/apply_file.py` —
  `Organization → CredentialType → Credential → Project → Inventory →
  JobTemplate → WorkflowJobTemplate → Schedule`.
- **Tests** use the in-memory `FakeAap` fixture (`tests/conftest.py`)
  for end-to-end CLI flows.

#### Deferred review items (post-`REVIEW.md`)

These were called out in the architectural review and intentionally
deferred. Each item names the trigger that would justify revisiting:

| Item | Trigger to revisit |
|---|---|
| Split `ResourceSpec` into domain (identity/fidelity/FKs/secrets) + infrastructure (api_path/transport) | A second consumer of resource contracts (offline validator, alternate backend, MCP server). |
| Move `RemoveRepo`/`EditWorkspace` `shutil`/`subprocess` calls behind infrastructure adapters | A second policy needs to plug in (dry-run, audit log, trash-before-delete). |
| Typed wrappers at the AWX use-case boundary (`ServerRecord`, `WritePayload`, `ActionPayload`) instead of `dict[str, Any]` | A second client implementation, or an MCP-served read path. |
| `workspace foreach` structured output (`--format`/`--columns`) | A scripted consumer hits the prefix-mixed stdout shape. |
| Bulk FK prefetch in save/apply | Bulk operations on 100+ resources start showing FK-resolution latency. |
| Decouple `AwxClient`/`cli/_context` from `untaped_core.Settings` (extractability) | Plan to ship `untaped-awx` as a standalone library or embed it elsewhere. |
| Workflow node graph round-trip (`save_hook`/`apply_hook` for `WorkflowJobTemplateNode`) — design space already shaped by polymorphic `FkRef` | User explicitly needs the v0.5 workflow-nodes milestone. |

## Development Workflow

```bash
# install / sync everything
uv sync --all-packages

# add a runtime dep to a specific package
uv add --package untaped-awx httpx-retries

# add a dev dep (root)
uv add --group dev some-test-helper

# run all tests with coverage
uv run pytest

# fast lint + format
uv run ruff check --fix && uv run ruff format

# strict types
uv run mypy

# run the CLI from source
uv run untaped --help

# install the `untaped` binary globally, editable across every workspace member
uv tool install --editable .

# common config interactions (operate on the active profile by default)
uv run untaped config list                          # show resolved settings (active ⤥ default ⤥ schema)
uv run untaped config list --all-profiles           # one row per (profile, key)
uv run untaped config list --show-secrets           # reveal redacted values
uv run untaped config set awx.token <token>        # write to the active profile
uv run untaped config set awx.token <tok> --profile prod  # write to a specific profile
uv run untaped config unset awx.token               # remove from the active profile

# profile management
uv run untaped profile list                         # list profiles, ✓ marks active
uv run untaped profile show prod                    # effective view (default ⤥ prod)
uv run untaped profile use prod                     # persist `active: prod`
uv run untaped profile create homelab --copy-from default
uv run untaped profile delete stage
uv run untaped profile rename prod production       # also updates `active:` if it pointed there

# per-call override (does not touch the persisted active profile)
uv run untaped --profile stage awx job-templates list
UNTAPED_PROFILE=stage uv run untaped config list
```

**TDD loop:**
1. Write the failing test (in `packages/untaped-<x>/tests/unit/`).
2. Run it; confirm it fails for the right reason.
3. Implement the smallest change that makes it pass.
4. Refactor with the test still green.

**Test layout:**
- Tests live in `packages/<pkg>/tests/unit/` (and `…/integration/` later).
- No `__init__.py` files inside `tests/` — pytest uses `--import-mode=importlib`.
- Mock httpx with `respx` (already a dev dep).
- For CLI tests, use `typer.testing.CliRunner`.

## Recipe: Add a new domain package

```bash
# 1. Create the package
uv init --package --lib packages/untaped-<X>

# 2. Add deps (typer + core; respx for tests already at root)
uv add --package untaped-<X> typer untaped-core

# 3. Build the 4 layers
mkdir -p packages/untaped-<X>/src/untaped_<x>/{cli,application,domain,infrastructure}
mkdir -p packages/untaped-<X>/tests/unit
```

Then:
- Implement `domain/models.py` — your entities/value objects.
- Implement `infrastructure/<x>_client.py` — concrete adapter using
  `untaped_core.HttpClient`.
- Implement `application/<use_case>.py` — define a `Protocol` for what you
  need, accept it in `__init__`.
- Implement `cli/commands.py` — Typer subapp, calls the use case, formats
  output. Add `@app.callback()` (single-command Typer apps collapse without
  it).
- Re-export `app` from `cli/__init__.py` and from the package
  `__init__.py`.
- **Register the domain in `src/untaped/main.py`**:
  ```python
  from untaped_<x> import app as <x>_app
  app.add_typer(<x>_app, name="<x>")
  ```
- Add the new package as a runtime dep of the root project:
  ```bash
  uv add untaped-<X>
  ```
- Update **this file** (`AGENTS.md`):
  - Add the package to the Repository Map and ownership table.
- Add the module to `[tool.mypy] packages = [...]` in the root
  `pyproject.toml` so the new package is type-checked.
- Run `uv sync && uv run pytest && uv run untaped --help` — confirm the
  new domain appears.
- If you have the global tool installed, re-run `uv tool install --editable .`
  to refresh it.

## Recipe: Add a new command to an existing domain

1. **Test first** in `packages/untaped-<X>/tests/unit/test_<feature>.py`.
2. If the command needs a new external call: add a method to the existing
   `infrastructure/<x>_client.py`. Don't create a new client class unless
   it's a different service.
3. If the command needs new domain logic: add an entity/method in
   `domain/`, a use case in `application/`.
4. Add the Typer command in `cli/commands.py`. Always:
   - accept `--format` (`OutputFormat`) and `--columns` options
   - support `--stdin` if the command takes a list of identifiers
   - decorate with `@app.command(..., no_args_is_help=True)` if it has
     required args
   - log to stderr; print only data to stdout
5. Run tests, lint, format. Verify in the integrated CLI:
   `uv run untaped <X> <new-command> --help`.

## Recipe: Add a new setting

1. Pick a section: top-level (rare) or one of the existing sub-models
   (`HttpSettings`, `AwxSettings`, `GithubSettings`, `WorkspaceSettings`).
   If it's a new bounded context, add a new sub-model to
   `untaped_core.settings` and wire it into `Settings`.
2. Use the right type:
   - `SecretStr | None = None` for any credential.
   - `Path | None = None` for filesystem paths.
   - `bool` / `int` / `str` with sensible defaults otherwise.
3. **Update tests in `packages/untaped-core/tests/unit/test_settings.py`**
   so the new key is loaded from YAML (under `profiles.default`) and
   overridable via env var. Remember: top-level keys are no longer
   honoured — every value lives under a profile.
4. **Update `packages/untaped-config/tests/unit/test_list_settings.py`**
   to assert the new key shows up in `untaped config list`.
5. Update **this file**'s "Cross-Cutting helpers", "TLS verification", or
   "Profiles" sections if the setting is cross-cutting.
6. Verify with `uv run untaped config list` — your new key must appear
   automatically, since the schema is walked.

## Output & Piping Conventions

- **stdout = data only.** Never print logs, prompts, or progress to stdout.
- **stderr = everything else.** Logs, progress bars, prompts. Loguru is
  configured to stderr.
- Every command exposes:
  - `--format / -f` (`json | yaml | table | raw`); default `table`
  - `--columns / -c` (repeatable) to project specific fields
  - `--stdin` to consume newline-separated identifiers from stdin (when
    the command takes a list)

**Pipeline examples** (the goal we're building toward):

```bash
# pick a job template interactively, then fetch its details as JSON
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format json

# launch every job template whose name matches "deploy-*" in parallel (later)
untaped awx job-templates list --format raw --columns name \
  | grep '^deploy-' \
  | untaped awx launch --stdin --parallel 5

# `cd` into a workspace (after eval'ing the shell-init snippet)
eval "$(untaped workspace shell-init zsh)"     # in your .zshrc
uwcd prod                                      # cd to the prod workspace dir

# morning routine: fetch + ff-only across every workspace
untaped workspace sync --all
untaped workspace status --all --format raw --columns workspace,repo,behind \
  | awk '$3 > 0 { print }'
```

If a command produces tabular data, it must be parseable by:
- `--format raw --columns x` → newline-separated single column
- `--format raw --columns x,y,z` → tab-separated rows (`cut -f1`, `awk` work)
- `--format json` → valid JSON array

## Decision Tree: Where does this code go?

1. **Is it shared across two or more domains?** → `untaped-core/`
   (or a new shared lib if it's a coherent subdomain, e.g.
   `untaped-tower-shared`).
2. **Is it CLI-only (argument parsing, output formatting)?** → `cli/`
   inside the domain package.
3. **Is it pure business logic — entities, value objects, invariants?** →
   `domain/` inside the domain package.
4. **Does it talk to an external service (HTTP, filesystem, subprocess)?** →
   `infrastructure/` inside the domain package.
5. **Does it orchestrate steps, translating between domain and
   infrastructure?** → `application/` inside the domain package.

If the answer is "I'm not sure" — search the existing code for similar
helpers before writing anything new.

## Common Mistakes

- **Importing httpx, pyyaml, or os in a `domain/` module.** Domain is pure.
  Move the call to `infrastructure/`.
- **A `cli/` module importing `infrastructure/` directly.** Go through
  `application/` so the use case is the only thing the CLI sees.
- **Adding a new dep with `pyproject.toml` edits.** Use `uv add --package`.
- **Writing a helper inside a domain that another domain will need.** Move
  it to `untaped-core` immediately (move ≠ copy).
- **Forgetting to register a new domain in `src/untaped/main.py`.** Test:
  does `uv run untaped --help` list it?
- **Adding a single-command Typer app without `@app.callback()`.** Typer
  collapses single-command apps into a flat command, breaking subcommand
  dispatch from the root CLI. Always add a no-op callback.
- **Adding a new setting without thinking about secrets and TLS.** If it's
  a credential, type it as `pydantic.SecretStr`. If it's a hostname for a
  service that uses TLS, the client must use `resolve_verify(s.http)`.
- **Forgetting `no_args_is_help=True` on commands with required args.**
  `untaped foo bar` (no args) must show help, not crash with a missing-arg
  error.
- **Naming a method `list` on a class with `list[X]` return annotations.**
  mypy resolves `list` to the method (a callable) inside the class, not
  the builtin, and complains about iteration / "not valid as a type". Use
  a different name (we use `entries`) or `from typing import List as _List`.
- **Adding a new git operation outside `GitRunner`.** All git subprocess
  calls live in `untaped_workspace.infrastructure.git_runner.GitRunner` —
  domain and application layers depend on its `Protocol`, so tests can
  stub it. Don't import `subprocess` directly elsewhere in the workspace
  package.
- **Auto-switching branches on `sync`.** Don't. The branch cascade is
  honoured at clone time only. If on-disk and target branches diverge,
  skip with a warning row — never `git checkout` for the user.
