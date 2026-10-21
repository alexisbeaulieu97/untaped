# Building a plugin

A plugin is a Python package that adds to `untaped`, under one name, a
command subtree (`untaped <name> ...`), settings, state, doctor checks, a
stub skill, guides for agents, contracts it owns and contracts it fills, each
optional. The root discovers plugins through the `untaped.plugins` entry-point group and
owns everything else: there is no second console script, config command or
profile command. See the [Glossary](../CONTRIBUTING.md#glossary).

A plugin imports from `untaped.sdk` and nothing else in `untaped`, except
`untaped.contracts` and `untaped.testing` for [contracts](./contracts.md);
see [SDK stability](./reference/conventions.md#sdk-stability). Any plugin,
first-party or not, may also import another plugin's declared `api` module,
under the rules in
[Depending on another plugin](./reference/conventions.md#depending-on-another-plugin).

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
    └── __init__.py
```

The package requires the current product and declares the entry-point group:

```toml
[project]
name = "untaped-acme"
version = "0.1.0"
description = "Acme plugin for untaped."
requires-python = ">=3.14.1"
dependencies = ["pydantic>=2.13.3,<3", "untaped>=11,<12"]

[project.entry-points."untaped.plugins"]
acme = "untaped_acme:SPEC"

[build-system]
requires = ["uv_build>=0.11.8,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "untaped_acme"
module-root = "src"
```

The module settings bind the `src/untaped_acme` layout to the project.
Package data below that module root, such as a folder of
[guides](./reference/agent-pages.md#guides), ships in the wheel.

A plugin has one name, lowercase words joined by single hyphens (`@` is kept
for later): `untaped-<name>` on PyPI, `untaped_<name>` to import, and `<name>`
as entry point, config section, command group and data directory. Names core
keeps, such as `core`, `http` or `config`, are quarantined (`reserved-name`).
The entry point names the module's `SPEC` constant, one self-validating
`PluginSpec`. Anything else, a function returning one included, is
quarantined (`not-a-spec`) and never called.

The plugin's `untaped` requirement (`Requires-Dist`) is the only
compatibility check: declare it (`untaped>=11,<12`, raising the floor to the
minor that added an API you use, for example `untaped>=11.1,<12`); without
one, nothing is checked. That range excludes pre-releases such as
`11.0.0a0`; to run on a pre-release core, declare `untaped>=11.0.0a0,<12`. A
running `untaped` outside that range quarantines the plugin. Installers
normally enforce the range, so this shows up mainly after upgrading `untaped`
past it. The [changelog](../CHANGELOG.md) says what each version added or broke.

## Settings and the plugin app

A plugin's config section is its name. Its `settings` model holds what users
tune per profile; a plugin that writes managed data also declares a `state`
model. The two field sets must be disjoint, and either may be left out.
`plugin_dir(SPEC)` (`~/.untaped/plugins/<name>/`) is where it keeps other
data. To rename a setting, see [Settings](./reference/conventions.md#settings).

A plugin that connects to a service marks what it can't run without, and
`setup`, `setup plan`, doctor and `auth` read the marks: `NeedsSetup` on an
`X | None = None` field, `Secret(env=..., suggest=...)` on each top-level
`SecretStr | None` secret. `untaped` adds each secret's command setting
(`acme.token_command`); a misplaced mark quarantines the plugin.
`connected_client(config, section="acme", base_url=config.base_url)` sends the
`token` secret (else the only one) as the bearer.

A complete plugin module, with a nullary app factory (leave it out for a
plugin with no commands), its stub skill and the `SPEC` the entry point
above names:

```python
# src/untaped_acme/__init__.py
from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter
from pydantic import BaseModel, ConfigDict

from untaped.sdk import (
    PluginSpec,
    SkillAsset,
    StdinOption,
    create_app,
    echo,
    get_config_section,
    read_identifiers,
)


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
    def import_command(
        names: Annotated[list[str] | None, Parameter(help="Repository full names.")] = None,
        /,
        *,
        stdin: StdinOption = False,
    ) -> None:
        """Echo repository names given as arguments or piped as github.repo records."""
        for name in read_identifiers(
            list(names or []), stdin=stdin, id_field="full_name", accept_kinds={"github.repo"}
        ):
            echo(name)

    return app


SPEC = PluginSpec(
    name="acme",
    app_factory=build_app,
    settings=AcmeSettings,
    skills=(
        SkillAsset(
            name="untaped-acme",
            description=(
                "Acme through untaped: its greeting and importing repository names. "
                "Use when the user wants the Acme greeting or to import identifiers, "
                "or mentions Acme."
            ),
        ),
    ),
)
```

The optional `help` field (one non-empty line) is its summary in the Plugins
panel of `untaped --help`. With it, the plugin is mounted lazily: `build_app()`
runs only when dispatched, so `untaped --help` never imports its CLI. A
factory that raises or returns something other than a cyclopts `App` then
fails that command, `--help` included, with exit 4, naming the plugin; other
commands and shell completion keep working. `untaped doctor` runs every
factory and reports a failing one as a quarantine row (`bad-app-factory`, or
`duplicate-kind`/`foreign-kind` for a kind reused or not under its name).
Without `help`, composition calls `build_app()` once at startup, a bad factory
quarantines the plugin, and the listing shows the built app's own help.

Importing the module must have no side effects (registration, filesystem,
network, `ContextVar`); the root owns registration and mounting.

## Commands and configuration

The root mounts the plugin app beneath its name; management stays at the root:

```bash
untaped acme hello
untaped profile create staging --copy-from default
untaped --profile staging config set acme.greeting "hello from staging"
untaped --profile staging acme hello
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

When your plugin's data moves, declare a `DirMigration` in
`PluginSpec.migrations` rather than asking users to move or delete it by hand.
`untaped setup migrate-dirs` previews every plugin's rows as one table,
confirms (`--yes`, `--dry-run`), then applies them in plugin-name order;
`untaped doctor` warns while any row would still move or delete something.

```python
from untaped.sdk import delete_migration, move_migration, plugin_dir, setting_guard

def data_moves():  # acme.data_dir now defaults to plugin_dir(SPEC) / "data"
    return [(Path("~/.acme-data").expanduser(), plugin_dir(SPEC) / "data")]

SPEC = PluginSpec(
    name="acme",
    ...,
    migrations=(
        move_migration("acme.data", "1.x data", data_moves, guard=setting_guard("acme", "data_dir")),
        delete_migration("acme.cache", "1.x cache", lambda: [Path("~/.acme-cache").expanduser()]),
    ),
)
```

`preview(ctx, options)` only reads: it returns `MigrationRow`s (`action`
`move`, `delete`, `keep` or `then`, with absolute `source` and `destination`
paths, `detail` and `bytes`), none once there is nothing left, and must work
with no settings; with `options.measure` false (doctor) it leaves `bytes` at
0. `apply(ctx, options)` does the work and returns its `MigrationOutcome`s; it
runs again on every `migrate-dirs`, so make it do nothing the second time.
Core applies nothing of a migration whose preview raised, nor of two
migrations when one would delete a directory the other moves or keeps.
`unsafe_dir(path)` says why a directory must never go whole (it holds home or
untaped's own files, or is a symlink); the helpers below keep such a path, and
any their `guard` refuses, as a `keep` row. Move data into `plugin_dir(SPEC)`. An
id is `<plugin>.<noun>`; a malformed row, an id of another shape or a repeated
one quarantines the plugin (`bad-migration`, `duplicate-migration`).
`move_migration` moves each `(source, destination)` pair `moves()` returns,
never over existing data unless `replaceable(destination)` says a command only
created it; `setting_guard` keeps a place a profile still sets; data that a
symlink or saved path points into needs its own `DirMigration`.
`delete_migration` is the whole row for a directory nothing reads any more,
and `old_dirs(default, section, key)` finds an old directory a deleted setting
may have moved: `retired_values` reads that setting from config.yml even after
its key left your settings model, until `config migrate` deletes it.
`untaped lint` runs each preview on an empty `HOME`; the example
plugin (`examples/untaped-hello`) has one row.

## Caching what an upstream answers

When a command reads data your plugin fetched from an upstream it can ask
again, declare a `Cache` and serve every read through it: see
[Caches](./caches.md), and for a database of it, [Stores](./caches.md#stores).

## Filling another plugin's contract

A plugin can answer another plugin's questions by filling a
[contract](./contracts.md) that plugin (the owner) declares. Start from a
scaffold:

```bash
untaped plugin list --contracts
untaped plugin new gitlab --fills workspace.repo_source
```

`plugin new` writes `untaped-gitlab/` in the current directory (`--path`
elsewhere, `--dry-run` to list the files first): the package with its `SPEC`
offering the provider to the owner, the provider (`providers/workspace.py`)
with every method of the contract and its docstring (the required ones as
stubs, the optional ones and the bridge commented out, since a stub would
count as filling them), a test running lint's `assert_clean`, `assert_fills`
with a list of samples to fill in and `assert_surface`, and a `pyproject.toml`
with the ranges and extra below (`untaped[lint]` in its `dev` group). It
never writes into an existing directory.

A plugin that fills a contract declares, besides its `untaped` range, the owner
under an extra named like the owner, with a range of the owner's versions:

```toml
[project]
dependencies = ["untaped>=11,<12", "pydantic>=2.13.3,<3"]

[project.optional-dependencies]
workspace = ["untaped-workspace>=11,<12"]
```

`untaped lint` fails a plugin with no `untaped` range, or with a
`provides` key no such extra matches. An installed owner outside the
extra's range quarantines that offer alone (`owner-out-of-range`). The
provider imports only `untaped.sdk`, `untaped.contracts`, `untaped.testing`
and the owner's `api` module.

Fill the stubs, add samples, drop the scaffold's `lint.live` ignore once the
methods return, then run the plugin's tests and `untaped lint` in its directory.
[Testing contracts](./contracts.md#testing-contracts) says what each check does.

## Experimental and deprecated commands

Mark what is not stable once, where it lives; `untaped` supplies the help
panel, the last `--help` line, the warning and the checks:

```python
from untaped.sdk import PluginSpec, create_app, deprecated, experimental

SPEC = PluginSpec(name="acme", ..., stability=experimental)  # whole plugin
lab = create_app(name="lab", help="Try things.", stability=experimental)  # a group

@app.command(name="put")
@deprecated(removed_in=3, replacement=set_command)  # the path follows a rename
def put_command() -> None: ...
```

- Mark a plugin on its spec, not its app: a lazy mount reads only the spec.
- Name the replacement as the function or app, or as text (a command like
  `"untaped acme set"`, a setting key or prose) when it is in another
  plugin or on a spec. Text must resolve.
- `removed_in` is your distribution's major, which the warning names
  ([every kind](./reference/deprecation.md)). A deprecated command shows with
  `untaped --deprecated --help` and warns once per run. `untaped lint`
  rejects a hand-typed `Experimental:` or `Deprecated:`, a misplaced or
  redundant mark and a stale replacement.
- Mark a setting on its field, as `Annotated[int, experimental]`; it
  inherits its plugin's mark, and `config list` lists it apart.

## Packaged skills

A plugin ships its agent skill as a stub: a `SkillAsset` with a name and a
description on `PluginSpec.skills`, which untaped writes from its own
template. The root lists and installs every composed plugin's skills (see
[Agent skills](./skills.md)), and [Writing a plugin's
skill](./plugin-skills.md) says what the description holds and where the
rest goes.

## Validation and checks

Before committing a plugin:

```bash
uv sync
uv run untaped --help
uv run untaped acme --help
uv run untaped plugin list
uv run untaped doctor
uv run untaped lint
uv run pytest
uv run mypy
uv run ruff check
```

Add `pytest_plugins = ["untaped.testing.plugin"]` to your top-level
`conftest.py` for an isolated `HOME`, config and environment in every test.
Call `untaped_lint.testing.assert_clean(NAME)` (from `untaped[lint]`, see
[Lint](./lint.md)) from the plugin's own tests,
`untaped.testing.assert_surface(SPEC, path)` to keep its commands, settings
and contracts from losing a line by accident (experimental), and
`untaped.testing.invoke_root(["acme", "hello"])` to run `untaped acme hello`
in-process against the installed plugins; it returns the exit code, the
captured output and `diagnostics`, the typed stderr lines the command emitted
(progress lines only under `--verbose`, or when the test advances the clock
with `untaped.testing.progress_clock()`).
Define `build_app` (the `app_factory`) in the plugin package's
`__init__.py`, because the checks scan that package. Declare writing commands
with `@writes` (or `@writes(destructive=True)`). To waive a rule on one line,
see [Enforcement](./reference/conventions.md#enforcement).

Test `SPEC.app_factory()` in isolation, assert that the entry-point name
matches `SPEC.name`, and exercise root config, profile, skill, pipe and error
paths. `untaped plugin list`, `untaped acme --help` and `untaped doctor` show
the composed surface and any quarantine reason: a malformed plugin is
quarantined so the other plugins still boot.
