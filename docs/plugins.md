# Building a capability provider

A capability provider is a Python package that adds one capability to
`untaped`: a command subtree run as `untaped <capability> ...`, one config
section, and optionally state, doctor checks and packaged skills. The root
discovers providers through the `untaped.capabilities` entry-point group and
owns everything else: there is no second console script, config command or
profile command.

Provider code imports from `untaped.sdk` and nothing else in
`untaped`; [`src/untaped/sdk.py`](../src/untaped/sdk.py)
is the authoritative API surface. The internal registry and other modules are
not an API and may change in any release.
First-party capabilities may also use each other's declared `api.py` modules;
those are internal to `untaped` and not part of the provider API.

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
    "untaped>=10,<11",
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

The module settings bind the `src/acme_provider` layout to the project. A
skill below that module root ships in the wheel; `source-include` also
carries it into a source distribution. Check that the wheel has it:

```bash
uv build --wheel
unzip -l dist/acme_provider-*.whl \
  | grep 'acme_provider/skills/untaped-acme/SKILL.md'
```

The entry-point name must equal `CapabilitySpec.name`. The resolved object
must be callable and return one `CapabilitySpec` when called without
arguments.

The provider's `untaped` requirement (`Requires-Dist`) is the only
compatibility check: declare it (`untaped>=10,<11`); without one, nothing is
checked. A running `untaped` outside that range quarantines the provider.
Installers normally enforce the range, so this shows up mainly after
upgrading `untaped` past it. The [changelog](../CHANGELOG.md) says what each
version added or broke.
First-party capabilities register exactly this way. When two providers claim
the same capability name or config section, all of them are quarantined and a
warning names every claimant: no provider can take over another's commands or
settings, and the result does not depend on install order. Uninstall one to
restore the other. A capability whose settings import another capability's
`api` (ansible imports github's) is quarantined with it when that import
fails.

## 2. Settings and the capability app

A capability owns one config section. Profile fields are what users tune; a
capability that writes managed data also declares a state model. The two
field sets must be disjoint.

A complete provider module, with a nullary app factory, one packaged skill
and the callable the entry point above names:

```python
# src/acme_provider/__init__.py
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from untaped.sdk import (
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


def provider() -> CapabilitySpec:
    """Entry-point provider discovered by the unified shell."""
    return SPEC
```

`CapabilitySpec` validates the name, section, Pydantic models and asset
tuples.

The optional `help` field (one non-empty line) is the summary in the root
command listing. With it, the capability is mounted lazily: `build_app()` runs
only when its command is dispatched, so `untaped --help` never imports its CLI.
A factory that raises or returns something other than a cyclopts `App` then
fails that command, `--help` included, with exit 4, naming the capability;
other commands and shell completion keep working. `untaped doctor` runs every
factory and reports a failing one as a `bad-app-factory` quarantine row, so
you find it without dispatching. Without `help`, composition calls
`build_app()` once at startup, a bad factory quarantines the provider, and the
listing shows the built app's own help.

The provider callable must have no side effects (registration, filesystem,
network, `ContextVar`); the root owns registration and mounting.

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

Config keys are fully qualified (`acme.greeting`). A capability reads and
writes only its own section; `http.*` and `ui.*` are shared root settings, and
`untaped config set` rejects state fields.

The root supplies `--profile`, `--verbose` and `--quiet`. Raise errors inside
`report_errors()` so the root prints its standard diagnostics and
[exit codes](./reference/exit-codes.md). Give your error classes a `category`
and `system` (your section name) as class defaults; see
[Raise with a category](./conventions.md#raise-with-a-category-or-inherit-one).
[Command and output conventions](./conventions.md) covers flags, messages,
exit codes and record shapes.

## 4. Stable helper surface

[`sdk.py`](../src/untaped/sdk.py) lists every export,
and each helper's docstring is its reference.
[Command and output conventions](./conventions.md) says which helper each rule
uses. Beyond those:

- Read settings with `app_context().section(name, Model)` or
  `get_config_section(name, Model)`, which validate only your section, rather
  than `app_context().settings`.
- For a token, declare `token_sources: ClassVar[TokenSources] =
  TokenSources(env=(...))` and a `token_command: TokenCommand = None` field
  beside `token` on your profile model; see [Tokens](configuration.md#tokens).
- For a domain-specific HTTP or filesystem adapter the API does not export,
  use your own dependency rather than an `untaped` internal.
- To keep a bare-repo cache, use `RepoCache` rather than your own git plumbing.
  Give your section its own root setting, resolve a repo's directory with
  `cache_path`, and use `locked()`, `ensure`, `fetch` and `run` on the cache.
  The `untaped.sdk` docstrings are the reference.

A row-producing command uses `FormatOption`, `ColumnsOption` and `emit`, and
namespaces its kind:

```python
from untaped.sdk import ColumnsOption, FormatOption, emit


@app.command(name="items")
def items_command(
    *, fmt: FormatOption = "table", columns: ColumnsOption = None
) -> None:
    rows = [{"id": "one", "label": "Example"}]
    emit(rows, fmt=fmt, columns=columns, kind="acme.item")
```

## 5. Piping

`--format pipe` writes the v1 envelope, one JSON object per line (see
[Pipes and record kinds](./reference/pipes.md)):

```json
{"untaped": "1", "kind": "acme.item", "record": {"repo": "octocat/Hello-World"}}
```

Kinds are the capability name and a snake_case noun, with an optional
`.summary` suffix for informational rows. For a `--stdin` command,
`read_identifiers()` reads bare identifiers or a pipe stream. Declare the
kinds you understand with `accept_kinds`, so a record of any other kind exits
2 instead of being misread:

```python
from untaped.sdk import read_identifiers

identifiers = read_identifiers(
    [], stdin=True, id_field="repo", accept_kinds={"github.repo"}
)
```

For whole records, `read_stdin_input(accept_kinds=...)` returns the bare
values or the parsed envelopes (a `StdinInput`). Both raise on empty stdin.
When an empty pipe (a filter that matched nothing) should do nothing, pass
`read_stdin_input(allow_empty=True)`: it returns no values, which the command
must treat as "nothing to do", never as "everything". A terminal stdin with
nothing piped still raises.

The provider joins pipelines with the first-party capabilities:

```bash
untaped github search repos --format pipe | untaped acme import --stdin
```

Put a filesystem destination in an absolute `record.target_path`, so
consumers such as `recipe apply --stdin` find it without knowing the
producer. Subclassing `TargetRecord` enforces this; `OutcomeRecord` fixes the
`action` field of mutation results. When `target_path` identifies the record,
re-declare it as `target_path: AbsolutePath` so it leads the output.

## 6. Packaged skills

Declare skill assets on `CapabilitySpec.skills`; the root lists and installs
them:

```bash
untaped skills list
untaped skills install acme --target codex
untaped skills install --all --target all
```

The short selector `acme` resolves the full ID `untaped-acme`. Write the
skill from the [skill template](./templates/SKILL.md), which holds the
skill rules; [Agent skills](./skills.md) covers installing.

## 7. Managed state

A capability that writes structured state declares a disjoint `state_model`
and writes through the state helpers:

```python
from untaped.sdk import StateCollection

_items = StateCollection("acme", "items", id_field="id")
_items.upsert({"id": "one", "label": "Example"})
```

State lives in the [state file](./configuration.md#file-and-layout), outside
profiles, and is never a user setting. The helpers keep other capabilities'
sections intact under the shared lock, so never read or write either file
directly.

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

Call `untaped.testing.check_conventions(NAME)` from the plugin's own tests.
Define `build_app` (the `app_factory`) in the capability package's
`__init__.py`, because the checks scan that package. Declare writing commands
with `@writes` (or `@writes(destructive=True)`). To waive a rule on one line,
see [Enforcement](conventions.md#enforcement).

Test the provider callable and `SPEC.app_factory()` in isolation, assert that
the entry-point name matches `SPEC.name`, and exercise root config, profile,
skill, pipe and error paths. `untaped capabilities`, `untaped acme --help` and
`untaped doctor` show the composed surface and any quarantine reason: a
malformed provider is quarantined so the other capabilities still boot.
