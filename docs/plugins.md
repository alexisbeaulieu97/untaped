# Building a capability provider

A capability provider is a Python package that adds one capability to
`untaped`: a command subtree run as `untaped <capability> ...`, one config
section, and optionally state, doctor checks and packaged skills. The root
discovers providers through the `untaped.capabilities` entry-point group and
owns everything else: there is no second console script, config command or
profile command. The docs say *capability* for what users run and
*provider* for the package; *plugin* is the informal word for either.

Provider code imports from `untaped.sdk` and nothing else in `untaped`; see
[SDK stability](./reference/conventions.md#sdk-stability). First-party
capabilities also use each other's declared `api.py` modules (see
[Depending on another capability](./reference/conventions.md#depending-on-another-capability));
those are internal to `untaped` and not part of the provider API.

[`examples/untaped-hello`](../examples/untaped-hello) in the repository is a
complete, tested plugin; copy it to start.

## Provider package

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
requires-python = ">=3.14.1"
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
checked. That range excludes pre-releases such as `10.0.0a0`; to run on a
pre-release core, declare `untaped>=10.0.0a0,<11`. A running `untaped` outside that range quarantines the provider.
Installers normally enforce the range, so this shows up mainly after
upgrading `untaped` past it. The [changelog](../CHANGELOG.md) says what each
version added or broke.

## Settings and the capability app

A capability owns one config section. Profile fields are what users tune; a
capability that writes managed data also declares a state model. The two
field sets must be disjoint. To rename a setting, see
[Settings](./reference/conventions.md#settings).

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

    model_config = ConfigDict(frozen=True)

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
            # SKILL.md's frontmatter `description` repeats this string exactly.
            description=(
                "Operates Acme through the `untaped acme` command (greeting, identifier"
                " import). Use when the user wants the Acme greeting or to import"
                " identifiers, or mentions Acme."
            ),
        ),
    ),
)


def provider() -> CapabilitySpec:
    """Entry-point provider discovered by the root."""
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

## Commands and configuration

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
[Raise with a category](./reference/conventions.md#raise-with-a-category-or-inherit-one).
[Conventions](./reference/conventions.md) covers flags, messages, exit codes
and record shapes.

A doctor check (`DoctorCheck` on `CapabilitySpec.doctor_checks`) returns a
`DoctorResult`. Its `fix` is the `untaped` command that repairs a failed or
warned row, without the program name: a string (`"config set acme.base_url
<URL>"`, split like a shell would) or an argv list. Write a value the user
supplies as a `<NAME>` placeholder. `doctor` emits it as the row's `fix`
argv, prefixed with `--profile NAME` unless it names one, so an agent can run
it as is. Set `automatic=True` only on a fix that meets the rule in the
`DoctorResult` docstring.

## Packaged skills

A capability ships its agent skill as a directory holding `SKILL.md`. Declare
it on `CapabilitySpec.skills`; the root lists and installs every composed
capability's skills (see [Agent skills](./skills.md)):

```bash
untaped skills list
untaped skills install acme --target codex
untaped skills install --all --target all
```

`SkillAsset.name` is the skill's full ID (`untaped-acme`; see
[Install skills](./skills.md#install-skills)). There is no core skill;
capability-specific guidance belongs in the capability's skill.

Start from this template. Copy it to
`src/<package>/skills/untaped-<capability>/SKILL.md`, replace every
UPPER_CASE placeholder, and declare it in `CapabilitySpec.skills`:

```markdown
---
name: untaped-CAPABILITY
description: Operates SYSTEM through the `untaped CAPABILITY` command (TASKS IN A FEW WORDS). Use when the user wants to INTENT, or mentions TRIGGER WORDS.
---

# untaped CAPABILITY

One or two sentences: the job this capability does, the judgement it needs,
and when another capability or tool fits better.

## Setup

Settings live under `profiles.<name>.CAPABILITY`. The user stores the token
by running `untaped auth set CAPABILITY` in their own terminal (the settings
model needs a `token_command` field for that). Check the connection with
`untaped CAPABILITY whoami`. Never ask for, print, echo or log tokens.

## Commands

| When you need to | Run |
|---|---|
| CONDITION | `untaped CAPABILITY NOUN list` |
| CONDITION | `untaped CAPABILITY NOUN get NAME` |
| CONDITION, after a preview | `untaped CAPABILITY NOUN delete NAME --dry-run` |

## Workflows

1. STEP, ending on something the agent can check.
2. Preview the change with `--dry-run` and show the user what it will touch.
3. After the user approves, rerun with `--yes`.

## Safety

- WHICH COMMANDS WRITE, which ask first, and how to preview each.
- Exit codes: 0 success, 1 failure or declined (`cancelled; no changes
  made`), 2 usage (including a write without a terminal and without
  `--yes`), 3 predicate hit, 4 fix the environment, 5 retry later, 130
  interrupted.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- A LIMIT, SURPRISING DEFAULT OR COMMON MISTAKE, with the reason.

## References

| File | Read it when |
|---|---|
| [references/TOPIC.md](references/TOPIC.md) | SITUATION |
```

Composition requires only a non-empty skill name and description; the rest
of this section is guidance, and no test checks the description's length or
voice. Keep `SKILL.md` short and split a long reference by task. The content
rule: a skill documents behaviour and judgement, not what the CLI prints. Say only what the agent cannot learn from the installed CLI,
and make the risky paths hard to get wrong.
`--help` and `--columns '?'` answer flags and fields; the skill says which
commands form a workflow, which order is safe, what the output means and what
to do next.

Frontmatter:

- `description` equals `SkillAsset.description` exactly. It routes rather
  than instructs: in the third person, what the skill covers, then the
  intents that should load it. Aim for under 60 words, on this capability's
  ground only, and without `": "`.
- `name` is the full ID, `untaped-<capability>`.

Content:

- The installed skill is the agent's whole manual. It never links to the
  repository's docs or source tree, which do not exist next to an installed
  CLI.
- It describes the version it ships with. Change it in the same release as
  the command, setting or contract it describes; history goes in the
  changelog.
- Every quoted `untaped ...` command should parse against the real CLI.
  Write synopses as `[--flag VALUE]`, `a|b`, `NAME` or `<name>`.
- Give exact commands only where a wrong flag is costly; elsewhere name the
  command and the intent.
- A destructive operation is a sequence: preview (`--dry-run`, `--check`, or
  list the selection), show the user what it will touch, scope it
  explicitly, wait for approval, then pass `--yes`. Say how to recover.
- Leave out `untaped skills ...` mechanics (the root's) and implementation
  notes, class names or test details.
- Examples use invented names (acme, Deploy, prod). Write calmly: a reason
  works better than capitals.

Shape:

- `SKILL.md` holds what every use needs, in about 900 words. What only some
  uses reach goes in `references/TOPIC.md`, one level deep, each pointer
  saying when to read it. A reference over about 100 lines opens with a list
  of its contents. Sample input files go in `examples/`.
- Keep behaviour test cases outside the skill directory, because
  `skills install` copies the whole folder. Rerun them when a change could
  alter what an agent does.

## Validation and checks

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

Add `pytest_plugins = ["untaped.testing.plugin"]` to your top-level
`conftest.py` for an isolated `HOME`, config and environment in every test.
Call `untaped.testing.check_conventions(NAME)` from the plugin's own tests,
and `untaped.testing.invoke_root(["acme", "hello"])` to run `untaped acme
hello` in-process against the installed providers; it returns the exit code
and captured output.
Define `build_app` (the `app_factory`) in the capability package's
`__init__.py`, because the checks scan that package. Declare writing commands
with `@writes` (or `@writes(destructive=True)`). To waive a rule on one line,
see [Enforcement](./reference/conventions.md#enforcement).

Test the provider callable and `SPEC.app_factory()` in isolation, assert that
the entry-point name matches `SPEC.name`, and exercise root config, profile,
skill, pipe and error paths. `untaped capabilities`, `untaped acme --help` and
`untaped doctor` show the composed surface and any quarantine reason: a
malformed provider is quarantined so the other capabilities still boot.
