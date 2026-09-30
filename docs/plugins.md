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
    "untaped>=9.0.0,<10",
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
`(3, 0)`, and `api_requires` is a `(min_inclusive, max_exclusive)` pair of such
tuples, compared as tuples (so `(1, 10)` is newer than `(1, 9)`). New exports
are additive and bump the minor version; removing or breaking an export bumps
the major, so `((3, 0), (4, 0))` stays compatible across 3.x. A provider that
relies on an export added in `3.N` declares `((3, N), (4, 0))`. A missing,
malformed or non-covering range quarantines the provider with an `api-range`
reason naming the running version. What each version added or broke is in
the [changelog](../CHANGELOG.md).

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
        for identifier in read_identifiers([], stdin=stdin, id_field="repo"):
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

    api_requires = ((3, 0), (4, 0))

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
errors so the root preserves its standard diagnostics and
[exit codes](./reference/exit-codes.md). Give your error classes a `category`
and `system` (your section name) as class defaults; see
[Raise with a category](./conventions.md#raise-with-a-category-or-inherit-one).
Follow [Command and output conventions](./conventions.md) for flags,
messages, exit codes and record shapes.

## 4. Stable helper surface

Provider imports come from `untaped.capability_api` only;
[`src/untaped/capability_api.py`](../src/untaped/capability_api.py) lists every
export, and each helper's docstring is its reference. The exports cover:

- composition types (`CapabilitySpec`, `SkillAsset`, `DoctorCheck`,
  `DoctorResult`) and `CAPABILITY_API_VERSION`;
- output, shared options and message wording (`emit`, `echo`, `FormatOption`,
  `plural`, `q`, `not_found`, `hint`, ...);
- errors and exit codes (`UntapedError`, `ErrorCategory`, `ConfigError`,
  `UsageError`, `attribution`, `note_failure`, `report_error`, ...);
- settings, context, tokens and doctor-check factories (`get_config_section`,
  `app_context`, `TokenSources`, `connection_check`, `online_check`, ...);
- HTTP, git, stdin and pipes, files, locks and state, UI, batches and
  concurrency.

[Command and output conventions](./conventions.md) says which helper to use
for each rule. Prefer `app_context().section(name, Model)` or
`get_config_section(name, Model)`, which validate only your section, over
`app_context().settings`. For a token, declare `token_sources:
ClassVar[TokenSources] = TokenSources(env=(...))` and a `token_command:
TokenCommand = None` field beside `token` on your profile model; see
[Tokens](configuration.md#tokens).

Use a provider's own dependency for domain-specific HTTP or filesystem adapters;
do not reach into `untaped` internals to obtain an unexported helper. For
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
{"untaped": "1", "kind": "acme.item", "record": {"repo": "octocat/Hello-World"}}
```

Kinds use the lowercase capability namespace and a snake-case noun, with an
optional `.summary` suffix for informational records. `read_identifiers()` can
consume bare identifiers or an untaped pipe stream when a command accepts
`--stdin`. Declare the kinds you understand with `accept_kinds`; a record of any
other kind exits 2 instead of being misread:

```python
from untaped.capability_api import read_identifiers

identifiers = read_identifiers(
    [], stdin=True, id_field="repo", accept_kinds={"github.repo"}
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

## 6. Packaged skills

Declare skill assets on `CapabilitySpec.skills`. The root discovers the union and
owns installation:

```bash
untaped skills list
untaped skills install acme --target codex
untaped skills install --all --target all
```

The short selector `acme` resolves the full asset ID `untaped-acme`. Write
the skill's `SKILL.md` from the [skill template](./templates/SKILL.md), which
holds the skill rules; [Agent skills](./skills.md) covers installing.

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
