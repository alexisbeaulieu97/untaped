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
- Make daily DevOps work composable: commands that emit data should support
  at least one mode whose stdout is suitable for piping into `fzf`, `jq`,
  `awk`, `cut`, or another `untaped` command. Not every command needs to
  be pipe-friendly — interactive flows, side-effecting actions with no
  meaningful return value, and human-only summaries are fine as-is.
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
    ├── untaped-core/             # shared infra (settings, http, config, profiles, output, stdin, errors)
    ├── untaped-config/           # `untaped config list/set/unset` (operates on profiles)
    ├── untaped-profile/          # `untaped profile list/show/use/create/delete/rename`
    ├── untaped-workspace/        # manage local git workspaces
    ├── untaped-awx/              # Ansible Automation Platform / AWX API
    └── untaped-github/           # GitHub authenticated-user inspection
```

| Package             | Type | Owns                                                                  |
| ------------------- | ---- | --------------------------------------------------------------------- |
| `untaped` (root)    | app  | The `untaped` binary; aggregates domain sub-apps via `add_typer`. Hosts the root `--profile` flag. |
| `untaped-core`      | lib  | Cross-cutting: settings, http (incl. TLS), config schema/file, profiles (resolver + helpers), output, stdin. |
| `untaped-config`    | lib  | The `config` meta-domain: introspect/edit profile contents in `~/.untaped/config.yml`. |
| `untaped-profile`   | lib  | The `profile` meta-domain: list/show/use/create/delete/rename profiles. |
| `untaped-workspace` | lib  | Workspace bounded context: per-workspace `untaped.yml` manifests, central `name → path` registry, sync/status/foreach via subprocess `git`. |
| `untaped-awx`       | lib  | AWX/AAP bounded context (jobs, templates, inventories, …).            |
| `untaped-github`    | lib  | GitHub bounded context — currently only the authenticated user (`whoami`); search/repos/etc. unimplemented. |

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
12. **Use absolute imports across the workspace.** `from untaped_<x>.… import …`
    or `from untaped_core import …`, never `from .foo import bar`. Enforced
    by ruff's `ban-relative-imports = "all"`; the rule applies inside every
    package, including tests.

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
| Add `--format` / `--columns` to a Typer command (data-emitting commands) | `from untaped_core import FormatOption, ColumnsOption` |
| Wrap a Typer command body so `UntapedError` → clean exit code 1 | `from untaped_core import report_errors`                  |
| Read piped values from stdin               | `from untaped_core import read_stdin`                            |
| Resolve identifiers from positionals or stdin (one source only) | `from untaped_core import read_identifiers` |
| Print a one-line message to stderr         | `typer.echo(msg, err=True)` — keep it boring; no helper          |
| Inject a stderr-warning hook into a use case | accept `warn: Callable[[str], None]` in `__init__`; `cli/` wires `typer.echo(f"warning: {msg}", err=True)` (see `packages/untaped-awx/src/untaped_awx/cli/_apply_runner.py:75`) |
| Raise a typed error                        | subclass `untaped_core.UntapedError`                             |
| Walk the Settings schema (for tooling)     | `from untaped_core.config_schema import walk_settings`           |
| Read/write `~/.untaped/config.yml`         | `from untaped_core.config_file import read_config_dict, write_config_dict, set_at_path, unset_at_path` |
| Atomic read-modify-write the config file   | `from untaped_core.config_file import mutate_config` (file-locked; use this for any new write path that touches the YAML) |
| Read/write a single profile                | `from untaped_core.config_file import read_profile, write_profile, list_profile_names, get_active_profile_name, set_active_profile, delete_profile, rename_profile` |
| Merge `default` ⤥ active to an effective dict | `from untaped_core import resolve_profiles` |
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

Other side-effecting calls (shell-out for `foreach`, editor launch for
`edit`, `rmtree` for `remove --prune` / `sync --prune`) live behind
`infrastructure.system_adapters` as three small adapter types
(`ShellRunner`, `EditorRunner`, `Filesystem`) plus default
implementations. `ShellRunner` and `EditorRunner` are `Callable`
aliases (they have one operation each); `Filesystem` is a `Protocol`
(it groups `rmtree` and any future side-effecting fs operation).
Application use cases require those adapters as constructor
arguments — none of them imports `subprocess` or `shutil` directly.
The CLI composition root wires the defaults; tests inject stubs.

`workspace foreach` honours the standard piping contract: `--format
table` (default) replays each repo's captured stdout / stderr with a
`[<repo>] line` prefix once that repo's command finishes (output is
buffered per repo by the underlying runner — chatty commands won't
appear until they exit). `--format json|yaml|raw` emits one
`ForeachOutcome` row per repo (including `command` and `duration_s`)
after every repo finishes.

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
  *domain* contract: `kind`, `identity_keys`, `canonical_fields`,
  `read_only_fields`, `fk_refs`, `secret_paths`, `actions`,
  `apply_strategy`, `fidelity`, `fidelity_note`. `apply_strategy` is
  a behaviour selector (a string the `StrategyResolver` maps to a
  concrete `ApplyStrategy`); it lives in domain because the choice of
  strategy is per-kind semantics, not transport. Application use
  cases depend only on this view. **`AwxResourceSpec`** (in
  `infrastructure/spec.py`) extends it with the AWX REST + CLI
  wiring: `cli_name`, `api_path`, `list_columns`, `commands`.
  Per-kind specs live in
  `infrastructure/specs/{job_template,workflow,project,credential,
  schedule,_support}.py` (each constructs an `AwxResourceSpec`) and
  are aggregated into `ALL_SPECS`. Spec fields stay honest with the
  CLI: a knob only lives in the spec if the factory actually wires it.
- **Typed boundary** (`domain/payloads.py`): `ResourceClient` reads
  return `ServerRecord` (Pydantic, `extra="allow"`, dict-style access
  preserved via `__getitem__`/`get`); writes accept `WritePayload`
  (create/update) or `ActionPayload` (custom actions). `request` and
  `request_text` keep raw `dict` returns as documented escape hatches.
  Strategies bridge: dicts produced by the apply pipeline are wrapped
  in `WritePayload` before calling the client; `ServerRecord` results
  are flattened via `model_dump()` for the in-place strip / diff /
  preserve passes.
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
- **`AwxConfig`** (`infrastructure/config.py`) is the package-local
  config struct (`base_url`, `token`, `api_prefix`,
  `default_organization`, `page_size`). Only `cli/` modules read
  `untaped_core.Settings` — today `cli/_context.awx_config_from_settings`
  (the composition root) and `cli/commands.py:ping_command`; new
  commands should follow the same pattern. `application/`,
  `infrastructure/`, and `domain/` depend on `AwxConfig` so
  `untaped-awx` is extractable as a standalone library.
- **Bulk FK prefetch** (`FkResolver.prefetch`): before the apply loop
  in `application/apply_file.py`, the FK plan derived from each doc's
  `fk_refs` is pre-fetched in one paginated `list` per `(kind,
  scope)`. Per-record lookups still fall through on cache miss;
  prefetch failures are best-effort (the per-call path is the
  authoritative one).
- **Restore fidelity tiers**: `full` (JT, Project, Schedule), `partial`
  (WorkflowJobTemplate), `read_only` (Credential, Organization, Inventory,
  CredentialType). Saves below `full` echo the tier to stderr and embed an
  inline YAML comment.
- **Apply ordering** for multi-doc files / directories: derived
  topologically from each spec's `fk_refs`
  (`application/apply_file._topological_sort`), with `ALL_SPECS` in
  `infrastructure/specs/__init__.py` as the tie-breaker — currently
  yielding `Organization → CredentialType → Credential → Project →
  Inventory → JobTemplate → WorkflowJobTemplate → Schedule`. The
  catalog-only stubs `ExecutionEnvironment`, `Label`, and
  `InstanceGroup` sit between `Inventory` and `JobTemplate` in
  `ALL_SPECS` for `FkResolver` lookups but are excluded from
  apply/save flows by their `commands=()` setting.
- **Tests** use the in-memory `FakeAap` fixture (`tests/conftest.py`)
  for end-to-end CLI flows.

### `untaped awx test` — declarative AWX test suites

`awx test run|list|validate` reads YAML files declaring a
parameterised matrix of launch payloads against one job template.
Designed to automate AWX-job testing: same playbook, many input
variations, one pass/fail report. v1 verdict = AWX `successful`
status; richer assertions are reserved for v2.

- **File shape**: YAML frontmatter (raw, **not** Jinja2-rendered)
  declares `variables`. The body is rendered with the resolved
  variable context (Jinja2 `StrictUndefined`, plus `to_yaml` /
  `to_json` filters), then parsed as YAML.
- **Cases**: a dict (`case_name → case_body`). Each case body has
  `launch:` (the AWX launch payload) and reserved `assert:`. v1
  rejects any non-empty `assert:` block at validation — assertions
  land in v2; reserved schema keeps that addition non-breaking. See
  `examples/test-deploy-app.yml`.
- **Variables**: `name`, `type` (`string`/`int`/`bool`/`choice`/`list`),
  `default`, `choices`, `secret`, `description`. Precedence high → low:
  `--var k=v` > `--vars-file` > `default` > interactive prompt.
  `--non-interactive` (or no TTY) → fail-fast on missing required
  vars instead of prompting.
- **Pass-through with typo warnings**: fields under `launch:` match
  AWX's API verbatim. Anything outside the v2.x known-fields set
  (`KNOWN_LAUNCH_FIELDS` in `application/test/resolver.py`) and not
  declared as an FK triggers a stderr warning ("unknown launch field
  'frooks' — typo? passing through to AWX") and still passes through.
- **Name resolution** (hybrid: `fk_refs` + `!ref` tag):
  - **Default path** — bare strings on FK fields resolve via
    `fk_refs` ∪ `launch_fk_refs`. `JobTemplate.fk_refs` covers
    `inventory`/`project`/`organization`/`credentials`. The
    launch-only fields `execution_environment`, `labels`, and
    `instance_groups` live under `launch_fk_refs` (in `domain/spec.py`).
    Resolution is **top-level only on declared FK fields, never
    recursive** — `extra_vars` and other opaque dicts are passed
    through untouched.
  - **Escape hatch** — the `!ref { kind, name, [scope...] }` YAML
    tag works anywhere in the tree (e.g. inside `extra_vars`).
    `RefSentinel` lives in `domain/test_suite.py`; the constructor
    is in `infrastructure/test/parser.py`. Structurally distinct
    from a dict, so user content like `{name: Alice}` is never
    misinterpreted.
  - **Catalog stubs** (`ExecutionEnvironment`, `Label`,
    `InstanceGroup` in `infrastructure/specs/_support.py`) exist
    purely so `FkResolver` can map names → ids; they have
    `commands=()` and no CLI sub-app.
- **Runner phases** (`application/test/runner.py`): `load → plan →
  prefetch → resolve → launch+wait`. Resolution finishes in the main
  thread before any worker is spawned (`FkResolver`'s caches aren't
  thread-safe). Workers only do `RunAction(spec, ..., payload=…)` +
  `WatchJob(job, timeout=…)` against a shared `AwxClient`
  (`httpx.Client` is documented thread-safe).
- **Result classification**: `result ∈ {pass, fail, error, timeout}`,
  separate from AWX's raw `job_status`. Exit code 0 only when every
  case has `result == "pass"`.
- **Wiring**: `cli/test_commands.py` is the composition root; it
  builds `LoadTestSuite` (with `DefaultParser`, `resolve_variables`,
  `TyperPrompt`), `ResolveCasePayload`, and `RunTestSuite` from
  `AwxContext`. The parser/vars-resolver/prompt are application-layer
  Protocols (`application/test/ports.py`); concrete adapters live in
  `infrastructure/test/`.

## Development Workflow

```bash
# install / sync everything
uv sync --all-packages

# add a runtime dep to a specific package
uv add --package untaped-awx httpx-retries

# add a dev dep (root)
uv add --group dev some-test-helper

# run all tests with coverage; gate is fail_under = 80% (see pyproject.toml)
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
- Tests live in `packages/<pkg>/tests/unit/`.
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
   - decorate with `@app.command(..., no_args_is_help=True)` if it has
     required args
   - log to stderr; print only data to stdout
   If the command emits data the user might want to pipe:
   - accept `--format` (`OutputFormat`) and `--columns` options so at
     least one machine-parseable mode is available (`raw` or `json`)
   - support `--stdin` if the command takes a list of identifiers
   Pure side-effect commands (e.g. `use`, `delete`, `rename`) and
   interactive-only flows can skip these — match existing peers in the
   same domain.
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
- **stderr = everything else.** Logs, progress bars, prompts. Stderr writes
  go through `typer.echo(msg, err=True)` — see Cross-Cutting helpers.
- **Commands that emit data** (lists, gets, status, …) should expose:
  - `--format / -f` (`json | yaml | table | raw`); default `table`
  - `--columns / -c` (repeatable) to project specific fields
  - `--stdin` to consume newline-separated identifiers from stdin (when
    the command takes a list)
- **Side-effect-only commands** (`use`, `delete`, `rename`, `apply --yes`,
  …) and interactive-only flows are exempt — they can print a short
  human confirmation to stderr and exit, with no `--format` knob.

**Pipeline examples** (the goal we're building toward):

```bash
# pick a job template interactively, then fetch its details as JSON
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format json

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
- **A `cli/` module calling business logic from `infrastructure/` instead
  of going through an `application/` use case.** Importing infrastructure
  to wire concrete adapters at the composition root (e.g.
  `cli/_context.py`) is fine and expected; the ban is on bypassing
  application use cases for the actual logic.
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
- **Naming a method `list` on a class whose annotations elsewhere include
  `list[X]`.** mypy resolves `list` to the method (a callable) inside the
  class, not the builtin, and complains about iteration / "not valid as
  a type". `Iterator[X]` / `Iterable[X]` returns don't collide and are
  fine. If you do hit the collision, use a different name (we use
  `entries`) or `from typing import List as _List`.
- **Adding a new git operation outside `GitRunner`.** All git subprocess
  calls live in `untaped_workspace.infrastructure.git_runner.GitRunner` —
  domain and application layers depend on its `Protocol`, so tests can
  stub it. Don't import `subprocess` directly elsewhere in the workspace
  package.
- **Auto-switching branches on `sync`.** Don't. The branch cascade is
  honoured at clone time only. If on-disk and target branches diverge,
  skip with a warning row — never `git checkout` for the user.
