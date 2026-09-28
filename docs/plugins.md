# Building a capability provider

`untaped` is a single application. Built-in capabilities are composed into the
root shell, and external capabilities are discovered from the
`untaped.capabilities` entry-point group. This page shows the provider workflow;
the implementation in
[`src/untaped/capability_api.py`](../src/untaped/capability_api.py) is the
authoritative API surface.

A capability contributes one command subtree, one config section, optional
state, optional doctor checks, and optional packaged skills. It runs as
`untaped <capability> ...`. A provider distribution does not add another
console script and does not own a second config or profile command group.

`untaped.capability_api` is the single public SDK surface: built-in and
external capabilities import untaped helpers from it and nothing else. Its
closed composition set and helper exports are intentional; provider code must
not import the internal registry or rely on other `untaped` modules as an API.
The `untaped.api` module and the `from untaped import X` root forwarding were
removed in 8.0 (capability API 2.0).

## 1. Provider package

An external provider is an ordinary Python distribution with one entry point
and no `[project.scripts]` section:

```text
acme-provider/
├── pyproject.toml
└── src/acme_provider/
    ├── __init__.py
    └── skills/
        └── untaped-acme/
            └── SKILL.md
```

The package requires the current product and declares the entry-point group:

```toml
[project]
name = "acme-provider"
version = "0.1.0"
description = "Acme capability for untaped."
requires-python = ">=3.14"
dependencies = [
    "pydantic>=2.13.3,<3",
    "untaped>=8.0.0,<9",
]

[project.entry-points."untaped.capabilities"]
acme = "acme_provider:provider"

[build-system]
requires = ["uv_build>=0.11.8,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "acme_provider"
module-root = "src"
source-include = ["src/acme_provider/skills/untaped-acme/SKILL.md"]
```

The explicit module settings keep the `src/acme_provider` layout bound to the
project. Keeping the skill below that module root makes it part of the wheel;
`source-include` also carries the file into a source distribution. After
creating the tree above, verify that the wheel carries the skill:

```bash
uv build --wheel
unzip -l dist/acme_provider-*.whl \
  | grep 'acme_provider/skills/untaped-acme/SKILL.md'
```

The entry-point name must equal the `CapabilitySpec.name`. The resolved object
must be callable, expose an `api_requires` range, and return one
`CapabilitySpec` when called without arguments. `CAPABILITY_API_VERSION` (in
`src/untaped/capability_api.py`) is a `(major, minor)` tuple of ints, currently
`(2, 1)`, and `api_requires` is a `(min_inclusive, max_exclusive)` pair of such
tuples, compared as tuples (so `(1, 10)` is newer than `(1, 9)`). New exports
are additive and bump the minor version; removing or breaking an export bumps
the major, so `((2, 0), (3, 0))` stays compatible across 2.x. A provider that
relies on an export added in `2.N` declares `((2, N), (3, 0))`. A missing,
malformed (for example the float bounds of 1.x) or non-covering range
quarantines the provider with an `api-range` reason naming the running
version. Version `2.0` (untaped 8.0) removed the `untaped.api` module and the
`from untaped import X` forwarding; 1.x ranges no longer compose. Version
`2.1` added `git_toplevel`, `file_lock`, `same_origin`, `UiContext.can_prompt`,
the `flag` option of `read_structured_file`, and the `allow_empty` flag of
`read_stdin_input`.

A built-in capability follows the same `SPEC` and `build_app()` shape but is
constructed in the `untaped` source tree and listed in the root composition.
It does not need an external entry point.

## 2. Settings and the capability app

A capability owns one config section. Profile fields are user-tunable; a
separate state model is required when the capability writes managed data. The
field sets must be disjoint.

The following is a complete provider module. It uses only the stable
`untaped.capability_api` surface for untaped imports, returns a real Cyclopts
app from a nullary factory, packages one skill, and exposes a callable provider
for the entry point above:

```python
# src/acme_provider/__init__.py
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from untaped.capability_api import (
    CapabilitySpec,
    SkillAsset,
    create_app,
    echo,
    get_config_section,
    read_identifiers,
)

if TYPE_CHECKING:
    from cyclopts import App


class AcmeSettings(BaseModel):
    """Profile-scoped Acme settings."""

    model_config = ConfigDict(extra="ignore")

    greeting: str = "hello from acme"


def build_app() -> App:
    """Return the capability command subtree without building it at import time."""
    app = create_app(name="acme", help="Acme capability commands.")

    @app.command(name="hello")
    def hello_command() -> None:
        """Print the configured Acme greeting."""
        settings = get_config_section("acme", AcmeSettings)
        echo(settings.greeting)

    @app.command(name="import")
    def import_command(*, stdin: bool = False) -> None:
        """Echo identifiers received as arguments or an untaped pipe."""
        for identifier in read_identifiers([], stdin=stdin, id_field="full_name"):
            echo(identifier)

    return app


SPEC = CapabilitySpec(
    name="acme",
    app_factory=build_app,
    config_section="acme",
    profile_model=AcmeSettings,
    skills=(
        SkillAsset(
            name="untaped-acme",
            source=Path(__file__).parent / "skills" / "untaped-acme",
            description="Use the Acme capability from the unified untaped CLI.",
        ),
    ),
)


class AcmeProvider:
    """Entry-point provider discovered by the unified shell."""

    api_requires = ((2, 0), (3, 0))

    def __call__(self) -> CapabilitySpec:
        return SPEC


provider = AcmeProvider()
```

`CapabilitySpec` validates the name, section, Pydantic models, and normalized
asset tuples. Composition invokes `build_app()` only after provider validation,
exactly once, and mounts the app it returned; a factory that raises or returns
something other than a cyclopts `App` quarantines the provider. The optional
`help` field (one non-empty line, default `None`) is the summary for the root
command listing. Built-ins set it so their factories run only when their
command is dispatched. An external's factory still runs during composition so
a bad factory is quarantined, and its listing shows the built app's own help.
The provider callable must have no registration, filesystem, network, or
`ContextVar` side effects; the root owns registration and mounting.

## 3. Commands and configuration

The root mounts the capability app beneath its name and keeps management at the
root:

```bash
untaped acme hello
untaped profile create staging --copy-from default
untaped --profile staging config set acme.greeting "hello from staging"
untaped --profile staging acme hello
untaped capabilities
untaped doctor
```

Config reads and writes use fully qualified `section.key` names. A capability
must not read or write another capability's section. Root `http.*` and `ui.*`
settings are shared profile fields; capability state is managed by the owning
capability and is rejected by `untaped config set`.

The root supplies position-independent `--profile`, `--verbose`, and `--quiet`
options. Use `report_errors()` for user-facing configuration, input, and domain
errors so the root preserves its standard diagnostics and exit codes: it exits
with the error's `exit_code`, which is `2` for `UsageError` and `1` for other
`UntapedError`s. Follow [Command and output conventions](./conventions.md) for
flags, messages, exit codes and record shapes.

## 4. Stable helper surface

Provider imports come from `untaped.capability_api` only. The module exports the
composition types (`CapabilitySpec`, `SkillAsset`, `DoctorCheck`, and related
records; `DoctorResult(..., warn=True)` reports a `warn` row that does not fail
`doctor`), `CAPABILITY_API_VERSION`, and the supported helpers including
`create_app`, `app_context`, `get_config_section`, `emit`, `read_identifiers`,
`report_errors`, `FormatOption`, and `ColumnsOption`. The canonical v1 wire
parser and record type are also exported as `parse_envelope_line` and
`PipeEnvelope`; capabilities retain their own kind and required-ID validation.

The shared runtime helpers are exported from the same module:

- Output and arguments: `echo`, `emit`, `render_rows`, `OutputFormat`,
  `raise_usage`, `parse_kv_pairs`, `parse_json_pairs`, `existing_file`,
  `resolve_each`, `clamp_parallel`, and `deprecated_alias` (a hidden old
  spelling of a renamed command or flag).
- Shared options: `FormatOption`, `ColumnsOption`, `YesOption`,
  `DryRunOption`, `StdinOption`, `ParallelOption` (>= 1), `LimitOption`
  (>= 1).
- Errors and exit codes: `UntapedError`, `ConfigError`, `UsageError` (exit
  2), `OperationCancelledError` (declined confirmation, exit 1), `HttpError`,
  `HttpStatusError`, `HttpTransportError`, `first_validation_error`, and
  `ExitCode`.
- Message wording: `plural`, `q`, `not_found`, `hint`, `summary`.
- Tokens: declare `token_sources: ClassVar[TokenSources] =
  TokenSources(env=(...))` and a `token_command: TokenCommand = None` field
  beside `token` on your profile model. `app_context().section(...)` and
  `get_config_section` then fill an unset `token` from `token_command` (run
  lazily, once per process) and then from the listed environment variables.
  See [Tokens](configuration.md#tokens).
- Doctor checks: `connection_check(id, section=...)` reports the resolved
  `base_url` and token source, and warns when the token is stored in plain
  text in `config.yml` (`<section>.token`); `executable_check(id, program, purpose=...)`
  warns when a program is not on `PATH`; `online_check(id, section=...,
  probe=...)` runs only under `untaped doctor --online` (and `untaped setup`):
  `probe` is a nullary callable doing your authenticated `whoami`-style call
  (import your CLI lazily inside it) that returns the pass detail and raises
  on failure. It runs inside `quick_probe()`, so `HttpClient` requests make
  one attempt with a timeout of at most 10 seconds. The check keeps one line
  of the error and names the fix (`config set <section>.token --prompt`,
  `<section>.base_url`, or `http.ca_bundle`). Your
  own `DoctorCheck(..., online=True)` is online-only too, and
  `DoctorResult(..., fix="config set acme.token --prompt")` appends the
  command to run to a failed or `warn` row.
- Records: `OutcomeRecord`, `TargetRecord`, `CheckRecord`, and the
  `UtcTimestamp` and `AbsolutePath` field types.
- Settings and context: `get_config_section`, `get_core_settings`,
  `HttpSettings`, `app_context`, `AppContext`.
- HTTP: `connected_client`, `HttpClient`, `RetryPolicy`, `resolve_verify`, the
  `paginate_link`, `paginate_offset`, and `paginate_pages` cursor loops, and
  `same_origin(url, base)` (whether a server-supplied link stays on `base`'s
  scheme, host and port; check it before following a link with credentials).
- Input and pipes: `read_identifiers`, `read_stdin_input`, `StdinInput`,
  `read_records`, `read_stdin`, `resolve_text_input`, `is_envelope_line`,
  `parse_envelope_line`, `PipeEnvelope`.
- Files and state: `atomic_write` (durable; keeps the file's mode unless
  given `mode=`, e.g. `mode=0o600` for owner-only files; writes through a
  symlink), `read_structured_file(path, flag=None)` (one YAML mapping, or JSON
  for a `.json` file, with string keys; `~` is expanded, a blank file is `{}`,
  and with `flag="--vars-file"` every error names the flag and file),
  `unified_diff_text`, `StateCollection`, `StateMap`, and
  `file_lock(path, *, timeout, error, busy, failed)`, a context manager holding
  an advisory lock file: when another process still holds it after `timeout`
  seconds it raises `error(busy)`, and when the lock file cannot be opened
  `error(f"{failed}: <reason>")`.
- UI: `UiContext` (including `success`, `styled`, `confirm_action`,
  `confirm_or_cancel`, `terminal` and `can_prompt`, which says whether its stdin
  is a terminal a prompt can read), `ui_context`, `ProgressHandle`, `PromptChoice`.
- Batches and concurrency: `batch_apply`, `BatchOutcome`, `finish`,
  `bounded_map`.

`bounded_map(fn, items, *, concurrency, on_each, on_abort=None,
while_running=None)` applies `fn` to every item on at most `concurrency` worker
threads (serially for one item or `concurrency=1`). `on_each(item, result)` runs
on the calling thread, in completion order when parallel, and exceptions from
`fn` propagate to the caller. On any escape, including Ctrl-C, queued work is
cancelled and `on_abort` runs before in-flight calls are awaited so the caller
can stop them. `while_running` runs on the calling thread after every item is
submitted, for foreground work such as draining a queue the workers feed.

`app_context().section(name, Model)`, `app_context().http`, and
`get_config_section(name, Model)` validate only the requested section (plus its
state section), once per context, so another capability's invalid settings
never break your commands. `app_context().settings` still validates every
section; prefer the section accessors.

`run_editor(path, *, argv=None, stdin=None, stdout=None, stderr=None)` opens an
external editor and waits for it to exit. Without explicit argv it parses
`VISUAL`, falling back to `EDITOR`, as shell-free arguments. Configure a GUI
editor with its wait flag. Omitted streams inherit the process streams; callers
can route all three streams to a controlling terminal to protect piped stdout.
The capability owns terminal requirements, temporary-file permissions, validation,
and cleanup. Launch failures raise `ConfigError`.

`run_git(args, *, timeout, cwd=None, capture=False, stdin=None, check=True,
auth_header=None, auth_url=None, retry_transient=False, ...)` runs one `git`
command non-interactively: stdin closed, terminal and credential-manager prompts
disabled, ssh in `BatchMode` unless the user configured ssh, C locale, and
inherited `GIT_DIR`-style variables dropped. It returns a `GitResult` and raises
`GitCommandError` (with `returncode`, `timed_out`, and redacted `stderr`) on a
missing binary, timeout, or non-zero exit. An `auth_header` (see
`git_auth_header(token)`) reaches git only through a private, temporary include
file, is redacted from errors, and disables Git trace variables.
`retry_transient=True` retries transport failures of idempotent network commands
with backoff. `safe_cache_path(url, root=...)` and `safe_path_segment(value)`
give deterministic cache paths that cannot escape `root`.
`git_toplevel(path)` returns the resolved root of the work tree containing the
directory `path`, or `None` outside any checkout; it raises `GitCommandError`
when git itself cannot run, so a missing git is never mistaken for "not a
checkout".

Use a provider's own dependency for domain-specific HTTP or filesystem adapters;
do not reach into `untaped` internals to obtain an unexported helper. Shared
settings, UI, error, and output behavior should use the stable exports. For
example, a row-producing command can use `FormatOption`, `ColumnsOption`, and
`emit` while retaining the capability namespace in its pipe kind:

```python
from untaped.capability_api import ColumnsOption, FormatOption, emit


@app.command(name="items")
def items_command(
    *, fmt: FormatOption = "table", columns: ColumnsOption = None
) -> None:
    rows = [{"id": "one", "label": "Example"}]
    emit(rows, fmt=fmt, columns=columns, kind="acme.item")
```

## 5. Piping

`--format pipe` emits the stable v1 NDJSON envelope, one object per line:

```json
{"untaped": "1", "kind": "acme.item", "record": {"full_name": "octocat/Hello-World"}}
```

Kinds use the lowercase capability namespace and a snake-case noun, with an
optional `.summary` suffix for informational records. `read_identifiers()` can
consume bare identifiers or an untaped pipe stream when a command accepts
`--stdin`. Declare the kinds you understand with `accept_kinds`; a record of any
other kind exits 2 instead of being misread:

```python
from untaped.capability_api import read_identifiers

identifiers = read_identifiers(
    [], stdin=True, id_field="full_name", accept_kinds={"github.repo"}
)
```

`read_stdin_input(accept_kinds=...)` returns either the bare values or the
parsed envelopes (a `StdinInput`), for commands that need whole records.
Both raise on an empty stdin. Pass `read_stdin_input(allow_empty=True)` when an
empty pipe (say, a filter that matched nothing) should do nothing instead: it
then returns no values, which the command must treat as "nothing to do", never
as "everything". A terminal stdin with nothing piped still raises.

A composed capability can participate in a root pipeline without another
executable:

```bash
untaped github search repos --format pipe | untaped acme import --stdin
```

Keep filesystem destinations in an absolute, non-empty `record.target_path`.
Consumers should not need producer-specific branching just to find that path.
Subclassing `TargetRecord` enforces this, and `OutcomeRecord` fixes the `action`
field of mutation results. When `target_path` identifies the record, re-declare
it as `target_path: AbsolutePath` so it leads the output.

## Confirmation

Gate destructive batches with `batch_apply(..., destructive=True,
assume_yes=yes)` and pass its outcome to `finish()`. A single confirmation uses
`ui.confirm_or_cancel(message, assume_yes=yes, refusal="<verb> requires --yes
when not interactive")`, which raises the decline for you. When stdin carries piped data, both prompt on the
controlling terminal. With no terminal they exit 2. A decline prints
`cancelled; no changes made` and exits 1. In tests,
`untaped.testing.invoke_cli(..., terminal=True, prompt_backend=...)` simulates
the controlling terminal. Without `terminal=True` there is none.

## 6. Packaged skills

Declare skill assets on `CapabilitySpec.skills`. The root discovers the union and
owns installation:

```bash
untaped skills list
untaped skills install acme --target codex
untaped skills install --all --target all
```

The short selector `acme` resolves the existing `untaped-acme` asset ID. The
installed directory and `.untaped-skill.json` marker retain the full asset ID.
See [Agent skills](./skills.md) for targets, scopes, overwrite behavior, and
marker paths. Start the skill's `SKILL.md` from the
[skill template](./templates/SKILL.md).

## 7. Managed state

When a capability writes structured state, declare a disjoint `state_model` and
use the stable state helper for the owning section. For example:

```python
from untaped.capability_api import StateCollection

_items = StateCollection("acme", "items", id_field="id")
_items.upsert({"id": "one", "label": "Example"})
```

State lives in the state file (`state.yml` beside `config.yml`, or `UNTAPED_STATE`),
outside profile overlays, and is never exposed as a user setting. The helpers
preserve other capabilities' sections under the shared state-file lock; never
read or write either file directly.

## 8. Validation and checks

Before committing a provider:

```bash
uv sync
uv run untaped --help
uv run untaped acme --help
uv run untaped capabilities
uv run untaped doctor
uv run pytest
uv run mypy
uv run ruff check
```

Test the provider callable and `SPEC.app_factory()` in isolation, assert that
its entry-point name matches `SPEC.name`, and exercise root config, profile,
skill, pipe, and error paths. A malformed external provider is quarantined so
other capabilities can still boot; a built-in provider violation is fatal.

The root validates provider metadata before mounting it. Exercise the provider
through `untaped capabilities`, `untaped <capability> --help`, and
`untaped doctor`; those commands expose the composed surface and any
quarantine diagnostics without maintaining a second option inventory here.
