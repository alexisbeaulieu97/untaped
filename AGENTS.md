# AGENTS.md — `untaped`

Single source of truth for how the `untaped` SDK is built. AI agents and
humans both read this file. This repo is the **SDK only**; each tool
(`github`, `jira`, `awx`, `ansible`, `workspace`, `apple-health`) lives in
its own repo with its own `AGENTS.md`. If you change the SDK surface or an
agent-facing workflow, update this file and `docs/` in the same commit.

## Mission

`untaped` is a batteries-included CLI **framework/SDK** built on cyclopts:
config, profiles, themes, consistent output, typed piping, HTTP/TLS, and
UI/prompt helpers. There is **no** central `untaped` command, **no** plugin
platform, **no** plugin registry/manifest/entry points, **no** managed
virtualenv, and **no** install script. See [`docs/decisions.md`](docs/decisions.md)
for the authoritative ADRs behind this direction.

Each tool is an **independent CLI** in its own repo. It depends on this SDK,
declares its own console script, and its `main()` calls `run_tool(app, spec)`.
Daily DevOps work composes — row-oriented `list`/`get`/`status`-style commands
are pipe-friendly via the `--format pipe` envelope (#3 below). We build *on
top of* existing CLIs (`gh`, `awx-cli`) where that is the right abstraction.

## Repository Map

The workspace root **is** the `untaped` SDK package. Tools live in separate
repositories and depend on this SDK via a git link.

```
untaped/
├── pyproject.toml                # the `untaped` SDK package
├── uv.lock                       # lockfile (commit it)
├── .python-version               # 3.14
├── .pre-commit-config.yaml
├── AGENTS.md                     # ← you are here (SDK rules)
├── CLAUDE.md                     # imports @AGENTS.md
├── README.md                     # human-facing intro
├── docs/                         # user-facing reference + decisions.md (ADRs)
├── src/untaped/                  # the SDK: api surface, config, run/tool, helpers
└── tests/                        # SDK tests
```

The SDK exposes its entire public surface from `src/untaped/api.py` (and
re-exported identically from the package root). Internal modules — `run.py`,
`tool.py`, `app_context.py`, `settings.py`, `config_file.py`, `cli.py`,
`http.py`, `output.py`, `pipe.py`, `ui.py`, `stdin.py`, `batch.py`, the
built-in `config/` and `profile/` groups — stay free to reorganize as long as
`untaped.api` keeps resolving.

Tools that depend on the SDK:

- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) — `untaped-awx`, Ansible Automation Platform / AWX.
- [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible) — `untaped-ansible`, Ansible dependency-graph workflows.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) — `untaped-github`, authenticated user and search.
- [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira) — `untaped-jira`, Jira Data Center workflows.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace) — `untaped-workspace`, local git workspace manifests.
- [`untaped-apple-health`](https://github.com/alexisbeaulieu97/untaped-apple-health) — `untaped-apple-health`, Apple Health export analysis.

Profiles **and** themes are built into the SDK; the standalone
`untaped-profile` and `untaped-themes` packages are **retired**.

## How a tool depends on and uses the SDK

There is no PyPI release yet, so tools depend on the SDK via a **git link**
pinned to a tag. In the tool's `pyproject.toml`:

```toml
[project]
dependencies = [
  "cyclopts>=4.16.0,<5",
  "untaped @ git+https://github.com/alexisbeaulieu97/untaped.git@v1.0.0",
]

[project.scripts]
untaped-github = "untaped_github.__main__:main"
```

The tool's `main()` is the composition root:

```python
from untaped.api import ToolSpec, create_app, run_tool

def main() -> None:
    app = create_app()
    # ... register the tool's commands on `app` ...
    run_tool(app, ToolSpec(
        command="untaped-github",
        section="github",
        profile_model=GithubProfileSettings,
        state_model=None,          # optional, disjoint from profile_model
        skills=(),                 # SkillAsset(...) entries, optional
    ))
```

Users install a tool directly from its repo:

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-github.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-github.git@v0.5.0  # pin a tag
```

## SDK public surface (`untaped.api`)

Tools import **only** from `untaped.api` (equivalently from the `untaped`
package root — identical surface). Reaching into SDK internals is not
supported. `__all__` in `src/untaped/api.py` is the contract:

- **Adding** a name to `__all__` is backwards-compatible (patch/minor).
- **Removing or renaming** a name (or changing its behaviour) is a **MAJOR**
  SDK version event.

The composition contract a tool builds on:

- `ToolSpec(command, section, profile_model, state_model=None, skills=())` —
  everything the SDK needs to run a tool, as data.
- `SkillAsset(name, source, description)` — one packaged agent skill.
- `register_tool(spec)` — registers the tool's settings section(s) for the
  process (validates profile/state models as disjoint).
- `build_tool_app(app, spec)` — wires `spec` onto a cyclopts app and returns
  it (drive `app.meta` directly in tests).
- `run_tool(app, spec)` — `build_tool_app` + run; use it as the tool's `main()`.

`run_tool` mounts per-tool command groups `<tool> config`, `<tool> profile`,
and `<tool> skills`; injects position-independent `--profile` / `--verbose`
root options (usable in any token position via leading-consume +
strip-on-unknown-option retry); and overrides the cyclopts app's display name
to the tool command.

Per-command settings are read via `app_context()`, which returns a frozen
`AppContext` with `.section(name, model)`, `.http`, and `.ui(strict=...)`.
(The old `plugin_context()` no longer exists.) Never declare a command-local
`--profile`.

## Config & state model

Shared config lives at `~/.untaped/config.yml`. **Format = v2 (SDK 2.x)** —
independently installed tools all read/write this file, so it is a cross-tool
contract; any change to its shape is a MAJOR SDK event. SDK 2.0 moved `http`
and `ui` out of the top-level "globals" position into ordinary per-profile
settings (the v1→v2 breaking change).

The v2 surface:

- **Top-level keys:** `active:`, `profiles:`, plus tool-owned top-level
  **state**.
- **`http` and `ui` are per-profile settings** (base fields on `Settings`,
  alongside `log_level`), addressed by dotted name (`http.verify_ssl`,
  `ui.theme`) and living under `profiles.<name>.http` / `profiles.<name>.ui`.
  A top-level `http:`/`ui:` block is ignored.
- **One section per tool** under the active profile, holding that tool's
  profile fields plus its disjoint, tool-managed **state** fields (the two
  field sets must not overlap).
- **Profile layering:** `profiles.default` sits beneath `profiles.<active>`;
  `http`/`ui` layer leaf-by-leaf like any other profile field.
- **Env-var override shape:** `UNTAPED_<SECTION>__<FIELD>` (uppercased
  section, double underscore before the field).

Command surface and routing:

- `<tool> config get/set/unset` manages scalar settings. **Bare config keys
  address the invoking tool's own section** — `untaped-github config set token X`
  writes `github.token` under the active profile.
- **`http.*` and `ui.*` are per-profile keys** addressed via their dotted
  prefix; they honour `--profile`/`--target-profile` like any profile key.
  There is no `--global` flag — target `--target-profile default` for a shared
  base value.
- Target a non-active profile for a write with `--target-profile`.
- **Tool-managed STATE fields are excluded from `config set`** (they are
  mutated by the tool, not the user).
- `<tool> config doctor` diagnoses the config: prints the path, active profile
  + source, known profiles, the resolved settings table, warns about any legacy
  top-level section that belongs under `profiles.default`, and exits non-zero if
  the file fails to load.
- `<tool> config edit` opens `~/.untaped/config.yml` in `$VISUAL`/`$EDITOR`
  (parsed with `shlex.split`), creating the parent dir if needed, and
  re-validates on save.
- `<tool> profile create|list|use|delete` manages the profile inventory
  (this is the absorbed, formerly-standalone profile workflow).

Writes are **surgical and filelocked** — never validate-and-rewrite the whole
file. Each tool validates **only its own section** (`extra="ignore"`), so an
older tool never clobbers a newer tool's section.

## Themes

Themes are built into the SDK as presets in `BUILTIN_THEMES`, selected via the
`ui.theme` config key. `ThemeSpec.color_roles` accepts Rich style strings for
`header`, `border`, `key`, `value`, `success`, `info`, `warning`, and `error`.
Those styles are emitted only for interactive terminal output; redirected
output stays plain text, and `json`/`yaml`/`raw`/`pipe` remain theme-independent.

## Agent skills

The SDK ships **no core agent skill**. Each tool ships its own `SKILL.md`,
declared as a `SkillAsset` in its `ToolSpec(skills=...)` and installed via that
tool's `<tool> skills` group. Guidance on building tools with the SDK lives in
the README and `docs/`, not in a packaged agent skill.

## Hard Rules

Non-negotiable. Every contribution must respect them.

1. **Keep `AGENTS.md` and `docs/` up to date.** If you change the SDK surface,
   the config/pipe contracts, a command/settings workflow, or a cross-cutting
   helper, edit the relevant docs in the same commit.
2. **Prefer `uv` commands over manual `pyproject.toml` edits.** Use
   `uv add`, `uv add --group dev`, `uv init --package --lib|--app`.
   Hand-editing is fine for tool config (`[tool.ruff]`, `[tool.mypy]`, …) but
   never for dependencies.
3. **The SDK public surface is `untaped.api`.** New public API goes in
   `__all__` there. Removing/renaming a name is a MAJOR version event — never
   do it casually. Internal modules may reorganize freely behind that surface.
4. **Write tests that verify the result.** TDD: failing test first, then
   implementation. Test through public APIs — never suppress warnings to
   access private members.
5. **Search before writing.** Grep `src/untaped` before implementing a helper.
   If it exists in the wrong place, *move* it (and update callers); don't fork.
6. **Finish each session with `uv run ruff check --fix && uv run ruff
   format`.** No exceptions.
7. **Cyclopts command signatures are explicit.** Use
   `Annotated[..., Parameter(...)]` for options/arguments and name public
   commands/options explicitly. Required inputs are required positional-only
   parameters (`Parameter(help=...)`, no `name=`, declared before `/`); a
   missing value renders `error: ... requires an argument` on stderr with exit
   2 via `run_cyclopts_app` — never emulate it with an optional default plus a
   manual help dance.
8. **Mark every secret as `pydantic.SecretStr`.** Tokens, passwords, API keys.
   `config list` redacts them; `repr(settings)` won't leak them in tracebacks.
   Call `.get_secret_value()` only at point of use.
9. **Use `resolve_verify(ctx.http)` for every httpx client.** Never hard-code
   `verify=True/False` or a path.
10. **Use absolute imports.** `from untaped import …` / `from untaped.api
    import …`, never `from .foo import bar`. Enforced by ruff's
    `ban-relative-imports = "all"`; applies to tests too.

## Tool DDD layout (for reference)

The SDK does not prescribe internals for tools, but the suite tools follow a
DDD layout. Tool repos document their own architecture; the shared convention is:

```
src/untaped_<x>/
├── __init__.py           # re-exports the cyclopts app
├── __main__.py           # main() → run_tool(app, ToolSpec(...))
├── cli/                  # cyclopts commands (thin)
├── application/          # use cases (orchestration); ports in application/ports.py
├── domain/               # entities, value objects (pure, no I/O)
└── infrastructure/       # external adapters (httpx clients, fs, …)
```

Import direction: `cli → application → domain` and `infrastructure → domain`;
`domain/` imports nothing from the other layers. A use case in `application/`
is unit-testable with a stub satisfying its `Protocol` — no httpx, no settings
file.

## Cross-Cutting helpers (`untaped.api`)

| Need                                       | Use                                                              |
| ------------------------------------------ | ---------------------------------------------------------------- |
| Resolve settings once per command (profile-aware, no global state) | `app_context()`; read sections with `ctx.section(name, Model)`, HTTP via `ctx.http`, UI via `ctx.ui(strict=...)` |
| Read a typed config section directly       | `get_config_section` |
| Build a cyclopts app                       | `create_app` |
| Build a validated HTTP client (token check + bearer auth + TLS + retries) | `connected_client` (enables a default `RetryPolicy()`; pass `retry=None` to disable or a custom policy to override) |
| Tune transient-failure retries (backoff, statuses, idempotency) | `RetryPolicy`; per-call override on `HttpClient.request(..., retry=...)` |
| Standard "setting not configured" error    | `missing_setting_error` |
| Walk paginated API collections             | `paginate_offset`, `paginate_pages` |
| Resolve TLS verify (OS trust + ca_bundle)  | `resolve_verify` |
| Make an HTTP call                          | `HttpClient` |
| Render semantic output/messages with active theme | `ctx.ui(strict=False)` / `ui_context`, `UiContext`, `ThemeSpec` |
| Prompt for typed interactive input         | `ctx.ui(strict=False).confirm/text/secret/select/multiselect(...)`; with `PromptChoice` |
| Add `--format` / `--columns` to a command  | `FormatOption`, `ColumnsOption` |
| Render `--format`/`--columns` row collections | `render_rows` (themed table for humans; theme-independent json/raw/pipe for pipes; pass `kind=` to tag `--format pipe` records; `empty="hint"` for a no-result note) |
| Emit a single entity OR a collection (shape-dispatched, writes stdout itself) | `emit(records, fmt=…, columns=…, empty=…, kind=…)` — a single model/dict renders as a vertical `key: value` detail (bare `{…}` under json/yaml); a sequence renders as a collection. Accepts pydantic models directly; prefer over `echo(render_rows([x.model_dump()], …))` for single-entity commands |
| Report progress for a slow operation       | `ctx.ui(strict=False).progress(label)` — spinner on TTY, throttled lines otherwise; `with ... as p: p.update(msg, fraction=..., new_phase=...)`; never wrap an interactive prompt; `ProgressHandle` |
| Reject bad usage with `error: ...` + exit 2 | `raise_usage` |
| Wrap a command body so `UntapedError` → exit 1 | `report_errors` |
| Read piped values from stdin               | `read_stdin` |
| Read a `--format pipe` stream into typed envelopes | `read_records` (`list[PipeEnvelope]`; `common_kind(...)` for the shared kind) |
| Resolve identifiers from positionals or stdin (one source only) | `read_identifiers` (pass `id_field=` to also accept a `--format pipe` stream) |
| Loop over identifiers with per-id `error: <id>: <exc>` rows | `resolve_each` |
| Run a mutating verb over a resolved set (preview → confirm → progress) | `batch_apply` (`destructive`/`assume_yes`/`preview_only`; returns `BatchOutcome` with `(item, result)` pairs + `planned_rows` — caller renders the summary and sets the exit code) |
| Parse repeated `KEY=VALUE` flags           | `parse_kv_pairs` |
| Clamp `--parallel N` at an upper bound      | `clamp_parallel` (caller supplies `cap` and `policy`) |
| Validate paths as cyclopts converters       | `existing_directory`, `existing_file` |
| Print raw logs / passthrough / low-level errors | `echo(msg, err=True)` |
| Raise a typed error                        | subclass `UntapedError` (also `ConfigError`, `HttpError`); `first_validation_error` for pydantic |
| Read core/section settings explicitly       | `get_settings`, `get_core_settings`, `HttpSettings`; `invalidate_settings_cache` from a root handler |
| Ensure / read / mutate config & tool state | `ensure_config`, `read_tool_state`, `mutate_tool_state` |

`PipeEnvelope`, `OutputFormat`, `AppContext`, `BatchOutcome`, `RetryPolicy`,
`ThemeSpec`, `UiContext`, and `ProgressHandle` are the public types backing the
rows above.

## Output & Piping Conventions

- **stdout = data only.** Never print logs, prompts, or progress to stdout.
- **stderr = everything else.** Logs, progress, prompts. Use `echo(msg, err=True)`.
- **`--verbose` / `-v`** is an SDK-injected root option (any token position).
  By default slow tool output is captured and shown only on failure;
  `--verbose` streams it live and raises the `untaped` logger to DEBUG.
- **`--quiet` / `-q`** is the inverse SDK-injected root option (any token
  position). It mutes the progress spinner and semantic `success`/`info`
  messages; `warning`/`error`, interactive prompts, data on stdout, and
  destructive-action confirmation previews are never muted.
- **HTTP error bodies are parsed for the human message.** When an error body is
  JSON with a `message`/`error`/`detail` or `errors[].message` field, the CLI
  shows `error: HTTP <code> … — <message>`; non-JSON/unrecognized bodies fall
  back to the raw `response: <body>` (also kept under `--verbose`).
- **Row-oriented data commands** (`list`, `status`, row-producing `get`, …)
  expose:
  - `--format / -f` (`json | yaml | table | raw | pipe`); default `table` for
    `list`, `yaml` for `get`.
  - `--columns / -c` (repeatable). Dotted paths supported.
  - `--stdin` to consume newline-separated identifiers when the command takes
    a list.
- **Scalar detail commands** may omit `--columns` and choose their own format
  default. `<tool> config get <key>` defaults to raw value output for scripting.
- **Side-effect-only commands** (`use`, `delete`, `rename`, `apply --yes`, …)
  and interactive flows are exempt — no `--format` knob.
- **`--format raw` without `--columns`** emits each row's first key. Every list
  use case promises the first key is the row's identifier so pipelines get the
  right value; reordering keys in a row source is a contract break.
- **`--all` vs `--all-<axis>`.** Bare `--all` means "iterate every instance of
  the noun the command targets". When a command iterates a *different* axis or
  changes view shape, use `--all-<axis>`.
- **`--follow --format json` always emits NDJSON** — one bare JSON object per
  line, no enclosing array. yaml/raw/table under `--follow` is per-line
  single-doc emission.
- **`--format pipe` is the typed interchange stream.** Self-describing NDJSON —
  one `{"untaped":"1","kind":<hint|null>,"record":{…}}` per line —
  meant to be read back by another tool (`read_records`, or
  `read_identifiers(..., id_field=…)`). The **v1 envelope is versioned
  independently of the SDK and FROZEN across SDK 1.x AND 2.x**; it carries its
  own version (`"untaped":"1"`) and any change bumps the envelope version. This
  is what guarantees `untaped-github | untaped-ansible` works across
  independently-installed tools built on different SDK versions. `pipe` emits the
  full record (ignores
  `--columns`); untagged producers emit `kind: null`. A consumer that
  *mutates* the piped set (e.g. `list --format pipe | delete --stdin --yes`)
  should route through `batch_apply` so the preview/confirm/`--yes`/progress UX
  is identical across tools; a destructive verb refuses a non-interactive stdin
  without `--yes` (the stream is the data, so there is nothing to confirm against).

## Development Workflow

```bash
uv sync                                         # install / sync the SDK
uv run pre-commit install                       # local lint at commit + mypy at push
uv add --group dev some-test-helper             # dev dep
uv run pytest                                   # tests with coverage (gate: 80%)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
```

The SDK has **no console script** — it is a library, so there is no
`untaped --help` to run from source. Drive behaviour through tests (build a
tool app via `build_tool_app` / `run_tool` and `CliInvoker`) or install a tool
that depends on it.

**Coverage measurement.** `--cov` is in `addopts`, so every `pytest` run
measures coverage (gate: 80%). Two non-obvious behaviours:

- `pytest --collect-only` reports ~31% coverage (import-time only); not a
  regression. Run plain `pytest` for the real number.
- For tight TDD loops, `pytest --no-cov` skips coverage and shaves a few
  hundred ms per run.

**TDD loop:**
1. Write the failing test in `tests/unit/`.
2. Run it; confirm it fails for the right reason.
3. Implement the smallest change that makes it pass.
4. Refactor with the test still green.

**Test layout:**
- SDK tests live in `tests/unit/`; use `tests/integration/` for real
  subprocess or fake-server fixtures.
- No `__init__.py` files inside `tests/` — pytest uses `--import-mode=importlib`.
- Mock httpx with `respx` (already a dev dep).
- For CLI tests, use `untaped.testing.CliInvoker`.

## Releasing

Keep `pyproject.toml` `version` equal to the **next** release; the latest git
tag equals the **released** version. **Bump `version` in the same PR as any
user-facing change** — a surface change, behaviour change, dropped name, or
dependency-floor change. Don't leave the bump (or release) as a follow-up.

SDK versioning: **adding** an `untaped.api` name is additive (patch/minor);
**removing or renaming** one — or changing the v1 config/pipe contracts — is a
**MAJOR** event.

Releases go through PRs, not direct pushes — the environment blocks `git push`
to `main` and tag pushes. The flow:

1. Branch; bump `version`; `uv lock`; gate (`ruff check` + `ruff format` +
   `mypy` + `pytest`); open a PR.
2. `gh pr merge --merge` once CI is green.
3. `gh release create vX.Y.Z --target main --title ... --notes ...` — creates
   the tag + release via the API (`gh` is not gated; plain `git tag` +
   `git push <tag>` is). If 1Password GPG signing is flaky, sign with
   `--no-gpg-sign`.

Cross-repo ordering for an SDK change that tools must adopt:

- **SDK first.** Tools resolve `untaped` from a git tag, so a tool only picks
  up new SDK behaviour after the SDK tag exists.
- **Bump each tool's `untaped @ git+...@vX.Y.Z` pin** to the new SDK release,
  relock (`uv lock --upgrade-package untaped`), gate, and **release the tool
  too** — the pin bump is itself a release-worthy dependency change.

## Decision Tree: Where does this code go?

1. **Reusable across multiple tools / part of the framework contract?** →
   `src/untaped/`, and expose it from `untaped.api` if tools should consume it.
2. **Tool-specific?** → it belongs in that tool's repo, not here.
3. Within a tool: CLI parsing/output → `cli/`; pure business logic → `domain/`;
   external service (HTTP, fs, subprocess) → `infrastructure/`; orchestration →
   `application/`.

## Conventions

- **Module docstrings.** Every source module (`*.py`) opens with a docstring
  describing what it owns. Re-export stubs (layer `__init__.py` files) are
  exempt — they're plumbing.
- **Re-export the public surface.** The SDK re-exports its full public API from
  `src/untaped/api.py` (and the package root) with an explicit `__all__`.
- **Per-command flags vs shared option types.** Per-command flags use
  `Annotated[..., Parameter(...)]` at the call site. Shared option types reused
  across commands live in the SDK as `Annotated[…, Parameter(…)]` aliases
  (`FormatOption`, `ColumnsOption`); use them in the annotation position, not as
  default values.
- **`errors.py` placement.** Packages with their own exception subclasses keep
  them in a top-level `errors.py`. Code that only raises `untaped` exceptions
  (`UntapedError`, `ConfigError`, `HttpError`) doesn't need one.
- **Lazy imports on CLI startup paths.** Heavy transitive imports that would
  pay on every `--help` are deferred into subcommand bodies. Add
  `# noqa: PLC0415` only when Ruff flags that specific import. Enforced by ruff
  (`extend-select = ["PLC0415"]`); tests are exempt.
- **mypy.** `[tool.mypy] plugins = ["pydantic.mypy"]` is configured; keep
  models pydantic-friendly. Run `uv run mypy` (strict) before pushing.

## Common Mistakes

- **Reaching into SDK internals from a tool.** Import from `untaped.api` only;
  internals move without notice.
- **Calling `plugin_context()`.** It is gone — use `app_context()`.
- **Importing httpx, pyyaml, or os in a `domain/` module.** Domain is pure;
  move the call to `infrastructure/`.
- **A `cli/` module calling `infrastructure/` business logic directly instead
  of through an `application/` use case.** Wiring concrete adapters at the
  composition root is fine; bypassing use cases for the logic is not.
- **Adding a dep with `pyproject.toml` edits.** Use `uv add`.
- **Adding a setting without thinking about secrets and TLS.** Credentials →
  `pydantic.SecretStr`. TLS clients → `resolve_verify(ctx.http)`.
- **Naming a method `list` on a class whose annotations include `list[X]`.**
  mypy resolves `list` to the method. Use `entries`, or `Iterator[X]` returns.
- **Overlapping profile and state fields in a `ToolSpec`.** The two field sets
  must be disjoint; `register_tool` validates them.
- **Removing or renaming an `untaped.api` name without a MAJOR bump.** That
  breaks every installed tool pinned to a 1.x tag.

## See also

- **Decisions (ADRs):** [`docs/decisions.md`](docs/decisions.md) — the
  authoritative record of the SDK-only direction.
- **User-facing docs:** [`docs/`](docs/README.md) — configuration, profiles,
  themes, piping.
- **Tools:**
  [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx),
  [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible),
  [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github),
  [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira),
  [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace),
  [`untaped-apple-health`](https://github.com/alexisbeaulieu97/untaped-apple-health)
