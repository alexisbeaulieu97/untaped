# Building a tool with the untaped SDK

This is a practical, code-forward guide to building a new standalone CLI on the
`untaped` SDK. For *why* the project is shaped this way — SDK-only, no central
command, frozen contracts — read [`docs/decisions.md`](./decisions.md). This
page is the how-to; that page is the rationale.

## 1. What the SDK is

`untaped` is a batteries-included CLI **framework** built on cyclopts. It gives
you config + profiles, themed output, `--format` rendering, NDJSON piping, and
HTTP/UI helpers. It is **not** an app: there is no central `untaped` command, no
plugin platform, no managed virtual environment, no install script. You don't
register an entry point or ship a manifest — you write a normal cyclopts app and
hand it to the SDK.

Each tool is an independent CLI named `untaped-<x>` that depends on the SDK and
installs into its own `uv tool` environment. Tools are versioned, installed, and
released independently — possibly against different SDK versions — but they
**share two interop contracts** so independently installed tools cooperate:

- **Config v2** — the `~/.untaped/config.yml` format (one section per tool;
  top-level `active:`/`profiles:`; per-profile `http:`/`ui:`/`log_level` under
  `profiles.<name>`; the `UNTAPED_<SECTION>__<FIELD>` env-var override shape).
  The layout changed v1→v2 at SDK 2.0 and is stable within SDK 2.x.
- **Pipe v1** — the `--format pipe` NDJSON envelope (see §7). Versioned
  independently of the SDK and stable across SDK 1.x **and** 2.x.

These contracts are what let `untaped-github | untaped-ansible` interoperate and
share one config file across independently installed tools. See decisions
[#3](./decisions.md) and [#4](./decisions.md).

You import everything you need from `untaped.api` (equivalently `untaped`). The
names in that module's `__all__` are the SDK contract; reaching into SDK
internals is not supported.

```python
from untaped.api import app_context, connected_client, render_rows, run_tool
# `from untaped import ...` is equivalent.
```

## 2. Project skeleton

A tool is an ordinary `uv` package. The one thing that makes it a CLI is the
console script pointing at your `main()`:

```toml
# pyproject.toml
[project]
name = "untaped-acme"
version = "0.1.0"
description = "Acme workflows built on the untaped SDK."
requires-python = ">=3.14"
dependencies = [
    "cyclopts>=4.16.0,<5",
    "pydantic>=2.13.3",
    # No PyPI yet: depend on the SDK via a git link, pinned to a tag.
    "untaped @ git+https://github.com/alexisbeaulieu97/untaped.git@v1.0.0",
]

[project.scripts]
untaped-acme = "untaped_acme.__main__:main"

[build-system]
requires = ["uv_build>=0.11.8,<0.12.0"]
build-backend = "uv_build"
```

When you're iterating on the SDK and the tool at the same time, add a
**dev-only** source override so `untaped` resolves to your local checkout
editable. This is a local convenience that does not affect what installed users
get (they get the git-pinned dependency above):

```toml
# Dev-only: work on the SDK and the tool together. Drop / ignore at release.
[tool.uv.sources]
untaped = { path = "../untaped", editable = true }
```

Lay the package out however you like; a thin `__main__.py`, a `cli/` package of
cyclopts commands, and a `settings.py` is the shape the suite tools use.

## 3. Define your settings model(s)

A tool owns one config **section** (its name, e.g. `acme`). Declare a
profile-scoped pydantic model for the user-tunable fields. Use
`extra="ignore"` so your tool validates only its own keys and never chokes on
keys another tool (or a newer build) wrote into the shared file:

```python
# untaped_acme/settings.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, SecretStr


class AcmeSettings(BaseModel):
    """Acme API settings (profile-scoped)."""

    model_config = ConfigDict(extra="ignore")

    base_url: str = "https://api.acme.example"
    token: SecretStr | None = None
```

If your tool keeps **tool-managed state** (data the tool writes itself, not
something the user sets), declare a second, **disjoint** model. The SDK's
registry validates that the profile and state field sets do not overlap; state
fields are never exposed to `config set`.

```python
class AcmeState(BaseModel):
    """Tool-managed state (the tool writes this, the user doesn't)."""

    model_config = ConfigDict(extra="ignore")

    last_sync: str | None = None
```

## 4. Build the app and `main()`

Build a cyclopts app with `create_app`, hang your commands off it, and make
`main()` call `run_tool(app, ToolSpec(...))`. That's the whole composition root.

```python
# untaped_acme/__main__.py
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from untaped.api import SkillAsset, ToolSpec, run_tool

from untaped_acme.cli import app
from untaped_acme.settings import AcmeSettings, AcmeState

SPEC = ToolSpec(
    command="untaped-acme",   # executable name; used in help + error text
    section="acme",           # config section this tool owns
    profile_model=AcmeSettings,
    state_model=AcmeState,    # omit (defaults to None) if you keep no state
    skills=(
        SkillAsset(
            name="untaped-acme",
            source=Path(str(files("untaped_acme").joinpath("skills", "untaped-acme"))),
            description="Use the untaped-acme CLI.",
        ),
    ),
)


def main() -> object:
    """Run the untaped-acme CLI."""
    return run_tool(app, SPEC)


if __name__ == "__main__":
    main()
```

`run_tool(app, spec)` is your `main()`. For free, it:

- registers your section(s) and the built-in profiles layout,
- mounts the shared `config`, `profile`, and `skills` command groups onto your
  app,
- wires position-independent `--profile` and `--verbose` root options (usable in
  any token position),
- renames the program to `spec.command` so help/usage/errors read
  `untaped-acme`, and
- runs everything under the SDK's error-reporting contract.

(`build_tool_app(app, spec)` is the wiring half — it returns the configured app
without running it, handy for tests that drive `app.meta` directly.)

Your command module is plain cyclopts plus SDK helpers:

```python
# untaped_acme/cli/__init__.py
from untaped.api import create_app

app = create_app(name="acme", help="Acme workflows.")
```

## 5. Writing command bodies

Resolve settings **once** per invocation via `app_context()`, then pull what you
need off the frozen context: your section, cross-cutting HTTP settings, and the
themed UI.

```python
from untaped.api import (
    ColumnsOption,
    FormatOption,
    app_context,
    emit,
    report_errors,
)

from untaped_acme.settings import AcmeSettings


@app.command(name="whoami")
def whoami_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """Show the authenticated Acme user."""
    with report_errors():
        ctx = app_context()
        config = ctx.section("acme", AcmeSettings)   # typed, profile-resolved
        ui = ctx.ui(strict=False)                    # themed UI; never fail data on a bad theme
        with AcmeClient(config, http=ctx.http) as client:  # see connected_client below
            with ui.progress("Fetching authenticated user…"):
                user = client.me()
        emit(user, fmt=fmt, columns=columns, kind="acme.user")  # single entity → detail view
```

Key conventions, all from the github tool:

- **HTTP clients go through `connected_client(...)`.** It validates required
  settings, applies bearer auth + TLS, and — crucially — raises the standard
  *command-aware* `ConfigError` for a missing setting:
  `acme.token is not configured (set it via untaped-acme config set token <token> …)`.
  Don't hand-roll that message.

  ```python
  from untaped.api import HttpSettings, connected_client

  class AcmeClient:
      def __init__(self, config: AcmeSettings, *, http: HttpSettings | None = None) -> None:
          self._http = connected_client(
              config,
              section="acme",
              headers={"Accept": "application/json"},
              http=http,
          )

      def me(self) -> dict[str, object]:
          return self._http.get_json_dict("/user")
  ```

  `connected_client(...)` enables a safe default `RetryPolicy()` automatically,
  so transient HTTP failures (connect errors, and `429`/`503` on idempotent
  methods) are retried with backoff. Pass `retry=None` to disable retries or a
  custom `RetryPolicy(...)` to override. Defaults:
  `max_attempts=3`, `backoff_base=0.5`, `backoff_max=30.0`,
  `retry_after_max=60.0`, `retry_statuses=(429, 503)`, `honor_retry_after=True`,
  `retry_on_transport=True`, and
  `idempotent_methods=frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})`.

  Two rules keep retries safe:

  - **Transport failures retry by phase.** A pre-send connect failure
    (`ConnectError`/`ConnectTimeout`/`PoolTimeout`/`ProxyError`) never reached
    the server, so it is retried for *any* method; a post-send read/write error
    may already have been processed, so it is retried only for
    `idempotent_methods`.
  - **`429`/`503` retry only for `idempotent_methods`.** A caller whose POST is
    genuinely idempotent (e.g. a search endpoint) opts in with
    `RetryPolicy(idempotent_methods=frozenset({"POST", "GET", ...}))`.

  `Retry-After` (integer seconds or HTTP-date) is honored, capped at
  `retry_after_max`; otherwise the delay is exponential backoff capped at
  `backoff_max`. A per-call override exists on `HttpClient.request(..., retry=…)`
  — a policy overrides, `None` disables, omitted inherits the client's policy.

- **Paginate with the SDK loops.** `paginate_pages(fetch, limit=...)` drives a
  cursor-style loop (`fetch` maps a cursor to `(items, next_cursor)`);
  `paginate_offset(http, "GET", path, item_key=..., limit=...)` walks
  `startAt`/`maxResults`-style offset envelopes. Both honor a `limit` and guard
  against non-converging paginators. `paginate_offset` also forwards a per-call
  `retry=` to each page fetch (default inherits the client's policy), so an
  idempotent `POST` collection such as a JQL search can opt that one endpoint
  into retry by passing a POST-inclusive `RetryPolicy`.

- **Render with `render_rows`.** It takes `fmt` (`--format`), `columns`
  (`--columns`), an optional `empty` hint, and a `kind` tag for pipe records.
  Use the `FormatOption` / `ColumnsOption` annotated types so every command
  exposes the same flags.

- **Prefer `emit(...)` for single-entity commands.** `emit` dispatches by shape
  and writes stdout itself, so you don't wrap it in `echo(...)` or call
  `.model_dump()` by hand:

  ```python
  from untaped.api import emit

  emit(user, fmt=fmt, columns=columns, kind="acme.user")
  ```

  A single model or dict renders as a vertical `key: value` **detail** view (a
  bare object `{...}` under `--format json`/`yaml`); a sequence renders as a
  **collection** (themed table, or a JSON array). It honors `empty=` for an
  empty sequence and emits the same `--format pipe` NDJSON envelope as
  `render_rows`. Prefer `emit(x, ...)` over
  `echo(render_rows([x.model_dump()], ...))` for `whoami`/`get`/`show`/`status`
  commands that return one entity — it gives them the proper detail view and
  removes the "forgot to `echo`" silent-no-output trap. `render_rows` is
  unchanged and still the right call for explicit row collections.

- **Wrap bodies in `report_errors()`** so `ConfigError`/`HttpError`/usage errors
  render as clean `error: …` lines with the right exit codes instead of
  tracebacks.

- **Reject bad usage with `raise_usage("…")`** (exit 2) for argument
  combinations cyclopts can't express on its own.

- **Required inputs are required positional-only parameters** declared before
  the `/` separator. A missing one renders
  `error: … requires an argument` on stderr with exit 2 automatically — never
  emulate that with an optional default plus a manual help dance.

  ```python
  @app.command(name="get")
  def get_command(repo: str, /, *, fmt: FormatOption = "table") -> None:
      """`repo` is required: omit it and cyclopts exits 2 with a usage error."""
      ...
  ```

For interactive input, use the UI context prompts:
`ctx.ui(strict=False).confirm/text/secret/select/multiselect(...)`. They require
a TTY and render on stderr, keeping stdout clean for data.

## 6. Tool-managed state

If you declared a `state_model`, write state through the SDK's safe state
surface — never reach into the config file. `mutate_tool_state(section, fn)`
mutates only your section's dict under the shared config lock; every other
section, and any keys in yours that `fn` doesn't touch, are preserved. The
section is removed when `fn` leaves it empty. `read_tool_state(section)` returns
a copy.

```python
from typing import Any

from untaped.api import mutate_tool_state, read_tool_state


def remember_last_sync(stamp: str) -> None:
    def _apply(state: dict[str, Any]) -> None:
        state["last_sync"] = stamp

    mutate_tool_state("acme", _apply)


def last_sync() -> str | None:
    return read_tool_state("acme").get("last_sync")
```

State fields are **not** exposed to `config set` — only profile fields are. The
two field sets must stay disjoint (the registry enforces this). Because the
config file is co-owned by every installed tool, going through these helpers is
what guarantees an older tool never clobbers a newer tool's data.

## 7. Piping

`--format pipe` emits the **v1** NDJSON envelope — one self-describing JSON
object per line:

```json
{"untaped": "1", "kind": "acme.user", "record": {"login": "octocat"}}
```

You get this for free from `render_rows(...)` when the user passes
`--format pipe`. Set `kind` to a stable, namespaced tag (`acme.repo`,
`acme.user`) so a downstream tool can recognize what it's receiving. On the
consumer side, the stdin helpers read these envelopes:

```python
from untaped.api import read_identifiers, read_records

# Pull one id field out of piped records (or bare lines):
repos = read_identifiers([], stdin=True, id_field="full_name")
# Or the full records:
records = read_records(stdin=True)
```

`id_field` + `kind` are how independently-installed tools chain:
`untaped-github search repos --format pipe | untaped-acme import --stdin`. The
envelope shape is versioned independently of the SDK and stable across SDK 1.x
and 2.x, so the producer and consumer need not share an SDK version.
`PipeEnvelope` and `common_kind` are exported if you parse envelopes yourself.

## 8. Packaging agent skills

Ship agent skills as `SkillAsset`s in your `ToolSpec` (each is a `name`,
`source` directory, and `description`). The SDK does not bundle a core skill;
each tool ships its own. Users install yours with:

```bash
untaped-acme skills install
```

`run_tool` mounts the `skills` group (`list` / `install`) onto your app
automatically — you only provide the assets.

## 9. Install and run

There is no central command and no PyPI yet. Install a tool straight from git
into its own `uv tool` environment:

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-acme.git
untaped-acme config set token <token>
untaped-acme whoami
```

Configure shared globals once and every tool sees them
(`untaped-acme config set http.verify_ssl false`, `… ui.theme dark`); profiles
work across tools the same way (`untaped-acme --profile work whoami`).

## Tools in the suite

- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  authenticated user and GitHub search commands.
- [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira) —
  Jira Data Center ticket workflows.
- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) —
  Ansible Automation Platform / AWX workflows.
- [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible) —
  Ansible dependency graph and impact-analysis workflows.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace) —
  local git workspace manifests and registry state.
- [`untaped-apple-health`](https://github.com/alexisbeaulieu97/untaped-apple-health) —
  Apple Health export sync and analysis.
</content>
</invoke>
