# Building a plugin

A plugin is a Python package that adds to `untaped`, under one name, a
command subtree (`untaped <name> ...`), settings, state, doctor checks,
packaged skills, contracts it owns and contracts it fills, each optional. The
root discovers plugins through the `untaped.plugins` entry-point group and
owns everything else: there is no second console script, config command or
profile command. See the [Glossary](../CONTRIBUTING.md#glossary).

A plugin imports from `untaped.sdk` and nothing else in `untaped`, except
`untaped.contracts` and `untaped.testing` for [contracts](./contracts.md);
see [SDK stability](./reference/conventions.md#sdk-stability). First-party
plugins also use each other's declared `api.py` modules (see
[Depending on another plugin](./reference/conventions.md#depending-on-another-plugin));
those are internal to `untaped` and not part of the plugin API.

[`examples/untaped-hello`](../examples/untaped-hello) in the repository is a
complete, tested plugin with commands; copy it to start one. To fill another
plugin's contract, start with
[`untaped plugin new`](#filling-another-plugins-contract).

## Plugin package

An external plugin is an ordinary Python distribution with one entry point
and no `[project.scripts]` section:

```text
untaped-acme/
├── pyproject.toml
└── src/untaped_acme/
    ├── __init__.py
    └── skills/
        └── untaped-acme/
            └── SKILL.md
```

The package requires the current product and declares the entry-point group:

```toml
[project]
name = "untaped-acme"
version = "0.1.0"
description = "Acme plugin for untaped."
requires-python = ">=3.14.1"
dependencies = [
    "pydantic>=2.13.3,<3",
    "untaped>=10,<11",
]

[project.entry-points."untaped.plugins"]
acme = "untaped_acme:SPEC"

[build-system]
requires = ["uv_build>=0.11.8,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "untaped_acme"
module-root = "src"
source-include = ["src/untaped_acme/skills/untaped-acme/SKILL.md"]
```

The module settings bind the `src/untaped_acme` layout to the project. A
skill below that module root ships in the wheel; `source-include` also
carries it into a source distribution. Check that the wheel has it:

```bash
uv build --wheel
unzip -l dist/untaped_acme-*.whl \
  | grep 'untaped_acme/skills/untaped-acme/SKILL.md'
```

A plugin has one name, lowercase words joined by single hyphens (`@` is kept
for later): `untaped-<name>` on PyPI, `untaped_<name>` to import, and `<name>`
as entry point, config section, command group and data directory. Names core
keeps, such as `core`, `http` or `config`, are quarantined (`reserved-name`).
The entry point names the module's `SPEC` constant, one self-validating
`PluginSpec`. Anything else, a function returning one included, is
quarantined (`not-a-spec`) and never called.

The plugin's `untaped` requirement (`Requires-Dist`) is the only
compatibility check: declare it (`untaped>=10,<11`, raising the floor to the
minor that added an API you use, for example `untaped>=10.1,<11`); without
one, nothing is checked. That range excludes pre-releases such as
`10.0.0a0`; to run on a pre-release core, declare `untaped>=10.0.0a0,<11`. A
running `untaped` outside that range quarantines the plugin. Installers
normally enforce the range, so this shows up mainly after upgrading `untaped`
past it. The [changelog](../CHANGELOG.md) says what each version added or broke.

## Settings and the plugin app

A plugin's config section is its name. Its `settings` model holds what
users tune per profile; a plugin that writes managed data also declares a
`state` model. The two field sets must be disjoint, and either may be left
out. `plugin_dir(SPEC)` (`~/.untaped/plugins/<name>/`) is where it keeps
other data. To rename a setting, see
[Settings](./reference/conventions.md#settings).

A complete plugin module, with a nullary app factory (leave it out for a
plugin with no commands), one packaged skill and the `SPEC` the entry point
above names:

```python
# src/untaped_acme/__init__.py
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from untaped.sdk import (
    PluginSpec,
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

    model_config = ConfigDict(frozen=True)

    greeting: str = "hello from acme"


def build_app() -> App:
    """Return the plugin command subtree without building it at import time."""
    app = create_app(name="acme", help="Acme plugin commands.")

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


SPEC = PluginSpec(
    name="acme",
    app_factory=build_app,
    settings=AcmeSettings,
    skills=(
        SkillAsset(
            name="untaped-acme",
            source=Path(__file__).parent / "skills" / "untaped-acme",
            # SKILL.md's frontmatter `description` repeats this string exactly.
            description=(
                "Operates Acme through the `untaped acme` command (greeting, identifier"
                " import). Use when the user wants the Acme greeting or to import"
                " identifiers, or mentions Acme."
            ),
        ),
    ),
)
```

The optional `help` field (one non-empty line) is the summary in the root
command listing. With it, the plugin is mounted lazily: `build_app()` runs
only when its command is dispatched, so `untaped --help` never imports its CLI.
A factory that raises or returns something other than a cyclopts `App` then
fails that command, `--help` included, with exit 4, naming the plugin;
other commands and shell completion keep working. `untaped doctor` runs every
factory and reports a failing one as a quarantine row (`bad-app-factory`, or
`duplicate-kind` for a reused record kind). Without `help`, composition calls
`build_app()` once at startup, a bad factory quarantines the plugin, and the
listing shows the built app's own help.

Importing the module must have no side effects (registration, filesystem,
network, `ContextVar`); the root owns registration and mounting.

## Commands and configuration

The root mounts the plugin app beneath its name and keeps management at the
root:

```bash
untaped acme hello
untaped profile create staging --copy-from default
untaped --profile staging config set acme.greeting "hello from staging"
untaped --profile staging acme hello
untaped plugin list
untaped doctor
```

Config keys are fully qualified (`acme.greeting`). A plugin reads and
writes only its own section; `http.*` and `ui.*` are shared root settings, and
`untaped config set` rejects state fields.

The root supplies `--profile`, `--verbose`, `--quiet` and `--deprecated`. Raise
errors inside `report_errors()` so the root prints its standard diagnostics and
[exit codes](./reference/exit-codes.md). Give your error classes a `category`
and `system` (your section name) as class defaults; see
[Raise with a category](./reference/conventions.md#raise-with-a-category-or-inherit-one).
[Conventions](./reference/conventions.md) covers flags, messages, exit codes
and record shapes; interactive screens are in [Screens](./screens.md).

A doctor check (`DoctorCheck` on `PluginSpec.doctor_checks`) returns a
`DoctorResult`. Its `fix` is the `untaped` command that repairs a failed or
warned row, without the program name: a string (`"config set acme.base_url
<URL>"`, split like a shell would) or an argv list. Write a value the user
supplies as a `<NAME>` placeholder. `doctor` emits it as the row's `fix`
argv, prefixed with `--profile NAME` unless it names one, so an agent can run
it as is. Set `automatic=True` only on a fix that meets the rule in the
`DoctorResult` docstring.

## Moving what an older version left

When a new version of your plugin keeps its data somewhere else, declare a
`DirMigration` in `PluginSpec.migrations` rather than asking users to move or
delete the old directory by hand. `untaped setup migrate-dirs` previews every
plugin's rows as one table, confirms (`--yes` skips it, `--dry-run` stops
after the preview), then applies them in plugin-name order; `untaped doctor`
warns while any row would still move or delete something.

```python
from untaped.sdk import DirMigration, MigrationOutcome, MigrationRow, delete_migration

SPEC = PluginSpec(
    name="acme",
    ...,
    migrations=(
        DirMigration(id="acme.data", title="data into the plugin's directory",
                     preview=preview_data, apply=apply_data),
        delete_migration("acme.cache", "1.x cache", lambda: [Path("~/.acme-cache").expanduser()]),
    ),
)
```

`preview(ctx, options)` only reads: it returns `MigrationRow`s (`action`
`move`, `delete`, `keep` or `then`, with `source`, `destination`, `detail`
and `bytes`), none once there is nothing left, and must work with no
settings (`ctx.settings` is `None` when yours don't validate). `apply(ctx,
options)` does the work and returns one `MigrationOutcome` per row it ran; it
runs again on every `migrate-dirs`, so make it do nothing the second time.
Move data into `plugin_dir(SPEC)`. An id is `<plugin>.<noun>`; a malformed
row, an id of another shape or a repeated one quarantines the plugin
(`bad-migration`, `duplicate-migration`). `delete_migration` is the whole
row for a directory nothing reads any more, and `old_dirs(default, section,
key)` finds an old directory a deleted setting may have moved:
`retired_values` reads that setting from config.yml even after its key left
your settings model, until `config migrate` deletes it. `untaped plugin
check` runs each preview on an empty `HOME`, and the example plugin
(`examples/untaped-hello`) has one row.

## Filling another plugin's contract

A plugin can answer another plugin's questions by filling a
[contract](./contracts.md) that plugin (the owner) declares. Start from a
scaffold:

```bash
untaped plugin list --contracts
untaped plugin new gitlab --fills workspace.repo_source
```

`plugin new` writes `untaped-gitlab/` in the current directory (`--path`
elsewhere, `--dry-run` to list the files first): the package with its
`SPEC` offering the provider to the owner, the provider
(`providers/workspace.py`) with every method of the contract and its
docstring (the required ones as stubs, the optional ones and the bridge
commented out, since a stub would count as filling them), a test running
`check_conventions` and `assert_fills` with a list of samples to fill in,
and a `pyproject.toml` with the ranges and extra below. It never writes into
an existing directory.

A plugin that fills a contract declares, besides its `untaped` range, the
owner under an extra named like the owner, with a range of the owner's
versions:

```toml
[project]
dependencies = ["untaped>=10,<11", "pydantic>=2.13.3,<3"]

[project.optional-dependencies]
workspace = ["untaped-workspace>=10,<11"]
```

`check_conventions` fails a plugin with no `untaped` range, or with a
`provides` key no such extra matches. An installed owner outside the
extra's range quarantines that offer alone (`owner-out-of-range`). The
provider imports only `untaped.sdk`, `untaped.contracts`, `untaped.testing`
and the owner's `api` module.

Fill the stubs, add samples, then run the plugin's tests and
`untaped plugin check gitlab` against the installed package.
[Testing contracts](./contracts.md#testing-contracts) says what each check
does, and how an owner tests its own contracts.

## Experimental and deprecated commands

Mark what is not stable once, where it lives; `untaped` supplies the help
panel, the last `--help` line, the warning and the checks:

```python
from untaped.sdk import PluginSpec, create_app, deprecated, experimental

SPEC = PluginSpec(name="acme", ..., stability=experimental)  # whole plugin
lab = create_app(name="lab", help="Try things.", stability=experimental)  # a group


@app.command(name="put")
@deprecated(replacement=set_command)  # the path follows a rename
def put_command() -> None: ...
```

- Mark a plugin on its spec, never on its factory's app: a lazy mount reads
  only the spec.
- Name the replacement as the function or app, or as text (a command like
  `"untaped acme set"`, a setting key or prose) when it is in another
  plugin or on a spec. Text must resolve.
- A deprecated command shows with `untaped --deprecated --help` and warns once
  per run. `check_conventions` rejects a hand-typed `Experimental:` or
  `Deprecated:`, a misplaced or redundant mark and a stale replacement.
- Mark a setting on its field, as `Annotated[int, experimental]`; it
  inherits its plugin's mark, and `config list` lists it apart.

## Packaged skills

A plugin ships its agent skill as a directory holding `SKILL.md`. Declare
it on `PluginSpec.skills`; the root lists and installs every composed
plugin's skills (see [Agent skills](./skills.md)):

```bash
untaped skills list
untaped skills install acme --target codex
untaped skills install --all --target all
```

`SkillAsset.name` is the skill's full ID (`untaped-acme`; see
[Install skills](./skills.md#install-skills)). The core `untaped` skill
covers untaped itself; plugin guidance belongs in the plugin's skill.

Write `SKILL.md` from the [skill template](./plugin-skills.md), which also
says what belongs in it.

## Validation and checks

Before committing a plugin:

```bash
uv sync
uv run untaped --help
uv run untaped acme --help
uv run untaped plugin list
uv run untaped doctor
uv run untaped plugin check acme
uv run pytest
uv run mypy
uv run ruff check
```

Add `pytest_plugins = ["untaped.testing.plugin"]` to your top-level
`conftest.py` for an isolated `HOME`, config and environment in every test.
Call `untaped.testing.check_conventions(NAME)` from the plugin's own tests,
and `untaped.testing.invoke_root(["acme", "hello"])` to run `untaped acme
hello` in-process against the installed plugins; it returns the exit code
and captured output.
Define `build_app` (the `app_factory`) in the plugin package's
`__init__.py`, because the checks scan that package. Declare writing commands
with `@writes` (or `@writes(destructive=True)`). To waive a rule on one line,
see [Enforcement](./reference/conventions.md#enforcement).

Test `SPEC.app_factory()` in isolation, assert that
the entry-point name matches `SPEC.name`, and exercise root config, profile,
skill, pipe and error paths. `untaped plugin list`, `untaped acme --help` and
`untaped doctor` show the composed surface and any quarantine reason: a
malformed plugin is quarantined so the other plugins still boot.
