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

The supported provider import surface is `untaped.capability_api`. Its closed
composition set and helper exports are intentional; provider code must not
import the internal registry or rely on other `untaped` modules as an API.

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

The package requires the current v4 product and declares the entry-point group:

```toml
[project]
name = "acme-provider"
version = "0.1.0"
description = "Acme capability for untaped."
requires-python = ">=3.14"
dependencies = [
    "pydantic>=2.13.3,<3",
    "untaped>=4.0.0rc1,<5",
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
must be callable, expose an `api_requires` tuple, and return one
`CapabilitySpec` when called without arguments. Check the current
`CAPABILITY_API_VERSION` in `src/untaped/capability_api.py` when choosing the
compatible range.

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

    api_requires = (1.0, 2.0)

    def __call__(self) -> CapabilitySpec:
        return SPEC


provider = AcmeProvider()
```

`CapabilitySpec` validates the name, section, Pydantic models, and normalized
asset tuples. Composition invokes `build_app()` only after provider validation.
The provider callable must have no registration, filesystem, network, or
`ContextVar` side effects; the root owns registration and mounting.

## 3. Commands and configuration

The root mounts the capability app beneath its name and keeps management at the
root:

```bash
untaped acme hello
untaped profile create staging --copy-from default
untaped config set acme.greeting "hello from staging" --target-profile staging
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
errors so the root preserves its standard diagnostics and exit codes.

## 4. Stable helper surface

Provider imports come from `untaped.capability_api` only. The module exports the
composition types (`CapabilitySpec`, `SkillAsset`, `DoctorCheck`, and related
records), `CAPABILITY_API_VERSION`, and the supported helpers including
`create_app`, `app_context`, `get_config_section`, `emit`, `read_identifiers`,
`report_errors`, `FormatOption`, and `ColumnsOption`.

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
`--stdin`:

```python
from untaped.capability_api import read_identifiers

identifiers = read_identifiers([], stdin=True, id_field="full_name")
```

A composed capability can participate in a root pipeline without another
executable:

```bash
untaped github search repos --format pipe | untaped acme import --stdin
```

Keep filesystem destinations in an absolute, non-empty `record.target_path`.
Consumers should not need producer-specific branching just to find that path.

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
marker paths.

## 7. Managed state

When a capability writes structured state, declare a disjoint `state_model` and
use the stable state helper for the owning section. For example:

```python
from untaped.capability_api import StateCollection

_items = StateCollection("acme", "items", id_field="id")
_items.upsert({"id": "one", "label": "Example"})
```

State is outside profile overlays and is never exposed as a user setting. The
root preserves other capabilities' sections under the shared config lock.

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
