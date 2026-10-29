# Building a plugin

A plugin is a Python package that adds to `untaped`, under one name, a
command subtree (`untaped <name> ...`), settings, state, doctor checks, a
stub skill, guides for agents, contracts it owns and contracts it fills, each
optional. The root discovers plugins through the `untaped.plugins`
entry-point group and owns everything else: there is no second console
script, config command or profile command. See the [Glossary](../CONTRIBUTING.md#glossary).

A plugin imports from `untaped.sdk` and nothing else in `untaped`, except
`untaped.sdk.contracts` and `untaped.sdk.testing` for [contracts](./contracts.md);
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
    ├── __init__.py
    └── plugin.py
```

The package requires the current product and declares the entry-point group:

```toml
[project]
name = "untaped-acme"
version = "0.1.0"
description = "Acme plugin for untaped."
requires-python = ">=3.14.1"
dependencies = ["pydantic>=2.13.3,<3", "untaped>=12,<13"]

[project.entry-points."untaped.plugins"]
acme = "untaped_acme.plugin:Acme"

[build-system]
requires = ["uv_build>=0.11.8,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "untaped_acme"
module-root = "src"
```

The module settings bind the `src/untaped_acme` layout to the project.
Package data below that module root ships in the wheel.

A plugin has one name, lowercase words joined by single hyphens (`@` is kept
for later): `untaped-<name>` on PyPI, `untaped_<name>` to import, and `<name>`
as entry point, config section, command group and data directory. Names core
holds, such as `untaped`, `config` or `profiles`, are quarantined (`duplicate-name`).
The entry point names the plugin class in a leaf module
(`untaped_acme.plugin:Acme`); a class composition refuses is quarantined
(`bad-plugin-class`) with every problem it names. Anything else, an 11.x
`PluginSpec` or a function returning a class included, is quarantined
(`not-a-plugin-class`) and never called.

The plugin's `untaped` requirement (`Requires-Dist`) is the only
compatibility check: declare it (`untaped>=12,<13`, raising the floor to the
minor that added an API you use, for example `untaped>=12.1,<13`); without
one, nothing is checked. That range excludes pre-releases such as
`12.0.0a0`; to run on a pre-release core, declare `untaped>=12.0.0a0,<13`. A
running `untaped` outside that range quarantines the plugin. Installers
normally enforce the range, so this shows up mainly after upgrading `untaped`
past it. The [changelog](../CHANGELOG.md) says what each version added or broke.

## Settings and the plugin class

A plugin's config section is its name. Its `settings` model holds what users
tune per profile, and rows it keeps for users are a `StateRows` the class
holds ([Keeping what users create](#keeping-what-users-create)). A command's
`self.ctx.plugin.dir` (`~/.untaped/plugins/<name>/`) is where it keeps other
data. To rename a setting, see [Settings](./reference/conventions.md#settings).

A plugin that connects to a service marks what it can't run without, and
`setup`, `setup plan`, doctor and `auth` read the marks: `NeedsSetup` on an
`X | None = None` field (set means not `None`); a misplaced mark quarantines
the plugin. `setup`'s form edits each marked field in the field its type
takes, and a marked model as its fields that have no default.

How a plugin keeps a server's secret (stored with the server it is sent to,
never in configuration) is in [Credentials](./credentials.md).

A complete plugin, with its stub skill and the class the entry point above
names:

```python
# src/untaped_acme/plugin.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Command, Plugin, Positional, StubSkill


class AcmeSettings(BaseModel):
    """Profile-scoped Acme settings."""

    model_config = ConfigDict(frozen=True)

    greeting: str = "hello from acme"


class Hello(Command[AcmeSettings]):
    """Print the configured Acme greeting."""

    name: Positional[str | None] = Field(None, description="Whom to greet.")

    def run(self) -> str:
        return f"{self.settings.greeting}, {self.name}" if self.name else self.settings.greeting


class Acme(Plugin):
    """Acme's greeting."""

    settings = AcmeSettings
    commands = (Hello,)
    skill = StubSkill(
        "Acme through untaped: its greeting. Use when the user wants the Acme "
        "greeting, or mentions Acme."
    )
```

The class docstring is the plugin's summary in the Plugins panel of `untaped
--help`, and each command's docstring is its help. Core mounts the commands
from the class's declaration, so `untaped --help` reads them without running
any. Commands are classes: their fields are the flags and arguments, and core
prints what `run` returns or yields ([Conventions](./reference/conventions.md#options)).

Importing the module must have no side effects (registration, filesystem,
network, `ContextVar`); the root owns registration and mounting.

## What core reads from a command

A command is a pydantic model, and its fields' types are its whole
interface: core reads each one and builds the flags, the help, the
`untaped docs` pages and the pipe kinds from them. Beyond pydantic, this is
everything an author needs to know.

| A field's type, or the class's | What core does with it |
|---|---|
| `str`, `int`, `float`, `bool`, `Path`, `Literal[...]`, an enum, `X \| None`, `list[X]` | An option named after the field. Its `description` is the help, and `Field` constraints validate it. A `bool` defaulting to true, or a `bool \| None`, also gets `--no-`. A list option repeats, one value per flag. |
| `Positional[X]`, or `Annotated[Positional[X], AllowHyphen]` | An argument, filled in field order. `AllowHyphen` lets a value start with `-`. |
| `Annotated[X, Short("-x")]` | The option's short flag. |
| `File[M]`, `ExistingFile`, `TextOrStdin`, `PathOrStdin`, `KeyValue`, `KeyJson` | Core's value types: a file parsed as `M` (with an input page and schema), a path that must exist, a value or `-` for stdin, and a `KEY=VALUE` or `KEY=<json>` pair. `Annotated[File[M], Written]` is a file the command writes. |
| `Annotated[Ref, Document(M)]` | The value names a document of `M` that the plugin finds and parses itself. The docs show `M`'s input page; core checks no file. |
| A pydantic model of the plugin's own | A bundle: its fields are flattened into the command, in a help panel named after the class. Its docstring is a guide, linked "Read first" from each command taking it. |
| An enum of the plugin's own with a docstring | The choices. Its docstring is a guide, as for a bundle. |
| `Output[R]` | `--format` and `--columns`. `R` is what `run` prints, its outputs page and its pipe kind. The default configures it: `Output(columns=...)`, `Output(format="raw")`, `Output(formats=("tree", "json"), render={"tree": ...})` and `Output(stream=True)`. |
| `Stdin[R]`, `Lines` | `--stdin`, reading identifiers or rows of those kinds; `Stdin(via=Contract.method)` also reads each provider's own records through the contract's bridge. `Lines` is raw lines, with no flag. |
| `Confirm`, `DryRun` | `--yes` and `--dry-run`, with the preview, prompt and refusal (`self.confirm(...)`); `DryRun` alone is `--dry-run` for a command that writes without asking. |
| `Refresh = Refresh(CACHE)` | `--refresh` and `--cached`, with help naming the cache. |
| `Parallel = Parallel(cap=, default=)`, `Limit` | `-j/--parallel` and `--limit`, with their caps and defaults. |
| A `@field_validator` or `@model_validator` | Runs after parsing. A failure exits 2 with its message; ``InvalidValue("use `{}`", Set.spelled(...))`` names a command by its class. |
| The class docstring | The summary, the behaviour and an `Examples` section, whose command lines are checked against the composed root. |
| `stability = experimental` or `deprecated(...)`, `aliases = (...)` | The help panel, the footer, the warning, and an old spelling kept as a hidden alias. |
| `run`'s return annotation | `Iterator[R]` prints rows as they come, `R` one record, `R \| Iterator[R]` either (the command's choice), `R \| None` a record or nothing, `str` or `str \| None` a line or nothing, and `None` nothing. |
| `Command[S]` | `self.settings`: the plugin's section, as the run's profile composes it, validated. The docs list the settings the command reads. |
| `self.ctx` | `diag`, `terminal`, `project`, `plugin` and `http`: handles core gives every command, never declared. |

Exit codes follow from what `run` raises: an `UntapedError` exits with its
category's code, a usage error (validators included) exits 2, and rows that
failed set exit 1. `raise Failed()` after the rows exits 1, and a check that
matched (`--check`, `--fail-on-match`) raises `Matched()`, exit 3. How a
command does its work (its client, its application code, its screens) stays
outside the declaration.

## Commands and configuration

The root mounts the plugin app beneath its name; management stays at the root:

```bash
untaped acme hello
untaped profile create staging --copy-from default
untaped --profile staging config set acme.greeting "hello from staging"
untaped --profile staging acme hello
```

Config keys are fully qualified (`acme.greeting`). A plugin reads and
writes only its own section; `untaped.http.*` and `untaped.ui.*` are untaped's own, and
`untaped config set` rejects state fields.

The root supplies `--profile`, `--verbose`, `--quiet` and `--deprecated`. Raise
an `UntapedError` from `run` and the root prints its standard diagnostics and
[exit codes](./reference/exit-codes.md). Give your error classes a `category`
and `system` (your section name) as class defaults; see
[Raise with a category](./reference/conventions.md#raise-with-a-category-or-inherit-one).
[Conventions](./reference/conventions.md) covers flags, messages, exit codes
and record shapes; interactive screens are in [Screens](./screens.md).

A doctor check (a `DoctorCheck` the plugin class holds) returns a
`DoctorResult`; the row is named after its check, so the result needs
no id. Its `fix` is the `untaped` command that repairs a failed or
warned row, without the program name: a command named by its class
(`Refresh.spelled()`, `Set.spelled(key(AcmeSettings).url, "<URL>")`), which
follows the command when it is renamed, a string split like a shell would, or
an argv list. Write a value the user supplies as a `<NAME>` placeholder.
`doctor` emits it as the row's `fix` argv, prefixed with `--profile NAME`
unless it names one, so an agent can run it as is. Set `automatic=True` only on a fix that meets the rule in the
`DoctorResult` docstring.

## Keeping what users create

What only your plugin holds, such as the records its commands create, is
[state](./state.md): typed rows in a `StateRows` your plugin class holds.

## Caching what an upstream answers

When a command reads data your plugin fetched from an upstream it can ask
again, declare a `Cache` and serve every read through it: see
[Caches](./caches.md), and for a database of it, [Stores](./caches.md#stores).

## Filling another plugin's contract

A plugin can answer another plugin's questions by filling a
[contract](./contracts.md) that plugin (the owner) declares. Start from a
scaffold:

```bash
untaped plugin contracts
untaped plugin new gitlab --fills workspace.repo_source
```

`plugin new` writes `untaped-gitlab/` in the current directory (`--path`
elsewhere, `--dry-run` to list the files first): the package with its plugin
class (`plugin.py`) filling the owner's contract, the provider
(`providers/workspace.py`) with every method of the contract and its docstring
(the required ones as stubs, the optional ones and the bridge commented out,
since a stub would count as filling them), tests mirroring the package (lint's
`assert_clean` and `assert_surface` beside the plugin, `assert_fills` with a
list of samples to fill in beside the provider) and a `pyproject.toml` with
the ranges below (`untaped[lint]` in its `dev` group). It never writes into an
existing directory.

A plugin that fills a contract declares, besides its `untaped` range, the owner
in its dependencies with a range of the owner's versions, since the provider
subclasses the owner's contract and can't load without it:

```toml
[project]
dependencies = ["untaped>=12,<13", "pydantic>=2.13.3,<3", "untaped-workspace>=12,<13"]

[project.entry-points."untaped.plugins"]
gitlab = "untaped_gitlab.plugin:Gitlab"
```

`untaped lint` fails a plugin with no `untaped` range, or one that fills a
contract of a plugin it doesn't require with a range. An installed owner outside the
range quarantines its fills of that owner alone (`owner-out-of-range`). The
provider imports only `untaped.sdk`, `untaped.sdk.contracts`, `untaped.sdk.testing`
and the owner's `api` module.

Fill the stubs, add samples, drop the scaffold's `lint.live` ignore once the
methods return, then run the plugin's tests and `untaped lint` in its directory.
[Testing contracts](./contracts.md#testing-contracts) says what each check does.

## Experimental and deprecated commands

Mark what is not stable once, where it lives; `untaped` supplies the help
panel, the last `--help` line, the warning and the checks:

```python
from untaped.sdk import Command, Group, Plugin, deprecated, experimental


class Put(Command):
    """Put a value."""

    stability = deprecated(removed_in=3, replacement=Set)  # the path follows a rename

    def run(self) -> None: ...


class Lab(Group):
    """Try things."""

    stability = experimental  # a group and every command in it
    commands = (Put,)


class Acme(Plugin):
    """Acme's commands."""

    stability = experimental  # the whole plugin
```

- Name the replacement as its class, or as text (a command like
  `"untaped acme set"`, a setting key or prose) when it is in another
  plugin. Text must resolve.
- `removed_in` is your distribution's major, which the warning names
  ([every kind](./reference/deprecation.md)). A deprecated command shows with
  `untaped --deprecated --help` and warns once per run. `untaped lint`
  rejects a hand-typed `Experimental:` or `Deprecated:`, a misplaced or
  redundant mark and a stale replacement.
- Mark a setting on its field, as `Annotated[int, experimental]`; it
  inherits its plugin's mark, and `config list` lists it apart.

## Packaged skills

A plugin ships its agent skill as a stub: `skill = StubSkill("...")` on its
plugin class with its description, which untaped writes from its own template
as `untaped-<plugin>`. The root lists and installs every composed plugin's
skills (see [Agent skills](./skills.md)), and [Writing a plugin's
skill](./plugin-skills.md) says what the description holds and where the rest
goes.

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

Add `pytest_plugins = ["untaped.sdk.testing.plugin"]` to your top-level
`conftest.py` for an isolated `HOME`, config and environment in every test.
Call `untaped_lint.testing.assert_clean(NAME)` (from `untaped[lint]`, see
[Lint](./lint.md)) from the plugin's own tests, and
`untaped.sdk.testing.assert_surface(Hello)` to keep its commands, settings
and contracts from losing a line by accident (experimental).
`untaped.sdk.testing.invoke_root(["acme", "hello"])` runs `untaped acme hello`
in-process against the installed plugins; it returns the exit code, the
captured output and `diagnostics`, the typed stderr lines the command emitted
(progress lines only under `--verbose`, or when the test advances the clock
with `untaped.sdk.testing.manual_clock()`).
A plugin class (`class Hello(Plugin)`) is tested through the fixtures (see
`examples/untaped-hello/tests`): `install(Hello, settings=...)` composes it
into the test's home; `.run(Greet())` and `.cli("hello say ada")` return a
`Ran`; `with hello.running() as run:` runs a block as a command of that
install runs, and `run.diagnostics` are the typed lines the block itself
shows, as `Ran.diagnostics` are a command's (a command run inside reports on
its own `Ran`), and `run.http` is the factory a client class takes
(`JiraClient(settings, http=run.http)`); `clock`, `terminal` (absent until
scripted) and `credentials` are the clock, terminal and password store
commands meet; and `check(install(Hello))` guards its composition checks.
To waive a rule on one line, see
[Enforcement](./reference/conventions.md#enforcement).

Exercise root config, profile, skill, pipe and error paths too.
`untaped plugin list`, `untaped acme --help` and `untaped doctor` show
the composed surface and any quarantine reason: a malformed plugin is
quarantined so the other plugins still boot.
