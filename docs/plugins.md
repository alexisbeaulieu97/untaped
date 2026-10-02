# Building a capability provider

A capability provider is a Python package that adds one capability to
`untaped`: a command subtree run as `untaped <capability> ...`, one config
section, and optionally state, doctor checks and packaged skills. The root
discovers providers through the `untaped.capabilities` entry-point group and
owns everything else: there is no second console script, config command or
profile command.

Provider code imports from `untaped.sdk` and nothing else in
`untaped`; [`packages/untaped/src/untaped/sdk.py`](../packages/untaped/src/untaped/sdk.py)
is the authoritative API surface. The internal registry and other modules are
not an API and may change in any release.
First-party capabilities may also use each other's declared `api.py` modules;
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

## Settings and the capability app

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
[exit codes](./scripting.md#exit-codes). Give your error classes a `category`
and `system` (your section name) as class defaults; see
[Raise with a category](#raise-with-a-category-or-inherit-one).
[Conventions](#conventions) covers flags, messages, exit codes and record
shapes.

## Conventions

Every `untaped` command, first-party or third-party, looks and behaves the same
way. Each rule below names the `untaped.sdk` helper that implements it; use
the helper rather than your own version.

A provider also follows these rules:

- A capability reads and writes only its own config section, state, skills
  and doctor checks.
- Layering is `cli → application → domain`, with `infrastructure → domain`;
  `domain` imports nothing from the other layers.
- Errors raise with a category and a system (see
  [Raise with a category](#raise-with-a-category-or-inherit-one)).
- Commands that write declare it with `@writes`, and a destructive one takes
  both `--yes` and `--dry-run` (see [Options](#options)).

[`check_conventions`](#enforcement) flags part of this:

- `foreign-section`: a `get_config_section(...)` or `.section(...)` call
  naming another capability's section as a string literal;
- `layer`: an import against the layer direction, and `settings`: settings
  resolved outside `cli`;
- `errors-module`, `exception-base` and `error-system`: no `errors.py`, an
  exception that is not an `UntapedError`, or a capability's base error
  class without a `system`;
- `undeclared-write`, `mutation-format` and `destructive-controls`: `--yes`
  or `--dry-run` without `@writes`, a declared write without `--format`, or
  a destructive command without both `--yes` and `--dry-run`.

It does not check state writes or an error's category. Composition, not
`check_conventions`, quarantines a provider whose skill name or doctor-check
ID duplicates another's. The message and option rules below have their own
checks, named in each violation line.

### Exit codes

[Exit codes](./scripting.md#exit-codes) defines what each code means. To
produce one:

- 0: return normally.
- 1: raise an `UntapedError` whose category is `failed`, `not_found`,
  `invalid` or `conflict` inside `report_errors()`, or call
  `finish(any_failed)`.
- 2: raise `UsageError` inside `report_errors()`, or call `raise_usage()`
  outside it.
- 3: call `finish(any_failed, predicate_hit=True)`.
- 4: raise with category `config` (`ConfigError`), `auth` or `permission`.
- 5: raise with category `unavailable` (`HttpTransportError` and 429/5xx
  statuses already are).
- 130: handled by the root shell.

`ExitCode` names these values.

Usage errors include conflicting flags, a value out of range, no selection,
and "requires `--yes` when not interactive". Problems that depend on
configuration or remote state are another `UntapedError`.

#### Raise with a category, or inherit one

Every `UntapedError` has a `category` (`ErrorCategory`: `usage`, `config`,
`auth`, `permission`, `not_found`, `invalid`, `conflict`, `unavailable`,
`failed`, `interrupted`) that selects the exit code, and a `system` that says
who is responsible (`untaped`, `local`, `git`, or the service section, such as
`awx`). A capability's error classes declare them as class defaults
(`category = ErrorCategory.NOT_FOUND`, `system = "awx"`); pass
`category=`, `system=`, `hint=` or `details=` to override one instance.

- `ConfigError` means **local setup** (settings, credentials, a missing tool)
  and exits 4. An invalid input *file* the command reads, or a value the user
  gave that fails validation, is `invalid` (exit 1), not config.
- HTTP errors take their category from the status and their `system` from
  `connected_client(section=…)`. A mapper that turns them into capability
  errors keeps both: `JiraApiError(msg, **attribution(err))`, and a 401 stays
  `auth` even when it becomes a `ConfigError` for its hint.
- Put a follow-up command in `hint=` (``"run `untaped config set awx.token --prompt`"``)
  rather than in the message; text output prints it as a `hint:` line.
- When a new error replaces a caught one, pass `**attribution(exc)` so the
  category, system, hint and details survive.
- A run exits with the most severe failure it saw (see
  [precedence](./scripting.md#precedence)).
  `report_errors()`, `resolve_each`, `batch_apply` and `report_error` note
  each failure they print, and `finish(any_failed)` exits with the most
  severe one. A failure that becomes a row instead is noted with
  `note_failure(exc)`.
- A failed row carries the structured failure next to its `detail`:
  `error=note_failure(exc, message=detail)` on `OutcomeRecord` and
  `TargetRecord` rows (`.model_dump(mode="json")` for a dict row). It returns
  the `ErrorInfo` and counts the failure; `ErrorInfo.from_exception` alone
  builds one without counting it. Keep the exception until the row is built;
  never flatten it into a string first.

See [exit codes](./scripting.md#exit-codes) for the category table.

### Messages (stderr)

stdout carries data only. Everything else goes to stderr. With `--format
json|yaml|pipe` (or `UNTAPED_DIAGNOSTICS=json`) every stderr line below is a
JSON object instead (see [stderr diagnostics](./scripting.md#stderr-diagnostics));
the helpers do this for you, so never print a JSON line yourself.

| Message | Shape | Helper |
|---|---|---|
| Error | `error: <msg>`: lowercase, no trailing period | Raise an `UntapedError`; `report_errors()` prints it. |
| Per-item error | `error: <item>: <msg>` | `resolve_each`, `batch_apply`, `report_error(exc, item=…)`, `report_row_errors(rows, item=…)` for failed rows |
| Not found | `<noun> not found: 'x'; known: a, b` | `not_found("profile", name, known=names)` |
| Quoted name | `'name'` | `q(name)` |
| Count | `3 repos`, never `repo(s)` | `plural(3, "repo")` |
| Hint | ``hint: run `untaped …` `` (or a short instruction that is not a command, hand-built as `hint: …`, like the bare-install hint) | `hint("config set awx.token --prompt")` |
| Warning | `warning: …` | `ui.message("warning", text)` |
| Success | Muted by `-q` | `ui.success(text)` |
| Summary | `<op>: 2 cloned, 1 failed` | `summary("sync", counts)` |
| Decline | `cancelled; no changes made` (exit 1) | `raise OperationCancelledError`, or `finish(outcome)` after `batch_apply` |
| Empty list | `No <plural> found.`, in table format only | `emit(rows, …, empty="No repos found.")` |
| Styled line | A Rich `Text` line for streamed human output (live job events); not muted by `-q` | `ui.styled(text)`, or `ui.styled(text, err=True)` for stderr; see its docstring for `tail=` and `truncate=` |

Do not call `echo()` for `error:` or `warning:` lines, `print()`, or build a
`rich.console.Console` yourself.

### Options

Use the shared option aliases instead of declaring your own copy:

| Alias | Flag |
|---|---|
| `FormatOption` | `-f/--format` (default `table`, or the user's `UNTAPED_FORMAT`/`ui.format`) |
| `ColumnsOption` | `-c/--columns` |
| `YesOption` | `-y/--yes`: skips only the prompt. `--dry-run` still wins. |
| `DryRunOption` | `--dry-run`: preview, then exit 0 |
| `StdinOption` | `--stdin` |
| `ParallelOption` | `-j/--parallel N`: N >= 1. Cap it with `clamp_parallel`. |
| `LimitOption` | `--limit N`: N >= 1 |

These short flags are reserved and have one meaning each: `-f --format`,
`-c --columns`, `-y --yes`, `-j --parallel`, `-o --out`, `-i --ignore-case`,
`-r --repo`.

Rules for parameters:

- Every parameter is either positional-only or keyword-only. Options are never
  positional.
- Every parameter has help text.
- Do not use `--empty-*` negative flags. Declare list options with `negative=""`.
- Negative `--no-*` flags exist only for booleans that default to true.
  Declare other booleans with `negative=""`.

To rename a command, group or flag, keep the old spelling as a hidden,
deprecated alias until the next major release:
`deprecated_alias(parent_app, "me", "whoami")` for a command or group, and
`deprecated_alias(command_app, "--old-flag", "--new-flag")` for a flag. The
root shell rewrites the old token and prints
``warning: `me` is deprecated and will be removed in the next major release; use `whoami` ``.
The old spelling never appears in `--help`. Aliases apply through the
`untaped` root, so test them with `build_root_app()`.

Command names are kebab-case; use plural nouns for collections. Names are
free, but a command that writes declares it with `@writes`, or
`@writes(destructive=True)` when it deletes or overwrites data:

- A command exposing `--yes` or `--dry-run` must be declared
  (`undeclared-write`).
- A declared write takes `--format` (`mutation-format`).
- A destructive command takes both `--yes` and `--dry-run`
  (`destructive-controls`).

### Confirmation and stdin

- Destructive batches go through `batch_apply(..., destructive=True,
  assume_yes=yes)`. It previews, then confirms. On a decline it sets
  `outcome.cancelled`, and `finish(outcome)` prints the decline line and
  exits 1.
- Single confirmations use `ui.confirm_or_cancel(message, assume_yes=yes,
  refusal="<verb> requires --yes when not interactive", preview=...)`, which
  raises `OperationCancelledError` on a decline (`ui.confirm_action` returns
  the answer instead).
- When stdin carries piped data, prompts read from the controlling terminal
  (`/dev/tty`). If there is no terminal, the command exits 2 and names
  `--yes`. In tests, `untaped.testing.invoke_cli(..., terminal=True,
  prompt_backend=...)` simulates that terminal; without `terminal=True` there
  is none.
- Read identifiers with `read_identifiers(names, stdin=stdin,
  id_field="…", accept_kinds={"<cap>.<noun>"})`. A pipe record of another
  kind exits 2. Empty stdin is an error. A command that acts on a filtered
  selection, where an empty selection is normal, may read it with
  `read_stdin_input(allow_empty=True)`: an empty pipe then does nothing,
  reports like an empty list, and exits 0.
- Before prompting, check `ui.can_prompt` on the `UiContext` that will prompt;
  without a terminal, fail with a hint naming the flag that supplies the value.
- Commands that need whole records or a mixed input use
  `read_stdin_input(accept_kinds=…)` or `read_records(accept_kinds=…)`.
  Never read `sys.stdin` directly.

### Output records

- The record is for scripts and agents; the table is for a human scanning
  rows. Keep every field on the record and pick the table's default columns
  on the record type, `table_columns: ClassVar[tuple[str, ...]] = (…)`: the
  identifying field, what changed or its state, and what the reader acts on
  next. Leave out fields that repeat the command's own arguments or are empty
  on most rows.
- A listed record with more than four fields (`error` aside) must declare
  default columns. `emit(rows, …, table_columns=[…])` overrides them for one
  command; a single record shows every field.
- Never pass defaults as `columns=` (`columns or DEFAULTS`):
  `--columns +name/-name` edits the defaults, and a named column is always
  shown, even when empty.
- Kinds are `<cap>.<singular_noun>` for entities and `<cap>.<verb>_outcome`
  for mutation results. Root commands use `untaped.*`. Each kind has exactly
  one schema.
- Fields are snake_case, with `id` and then `name` first. `url` is the web
  URL and `api_url` is the API link.
- A record's own fields come before the fields it inherits from the bases
  below, so its identifying field leads the table and `--format raw`. An
  inherited `action` follows it (after `name` when the record leads with
  `id` and `name`).
- Base mutation results on `OutcomeRecord`. `action` uses this vocabulary:
  `planned`, `created`, `updated`, `deleted`, `unchanged`, `skipped` (never a
  failure), `failed`, `partial`, `conflict`, `cancelled`, plus any
  domain-specific past-tense verbs.
- Base records about files or directories on `TargetRecord`, which requires an
  absolute `target_path`.
- `error` is reserved: both bases give a failed row an optional `error`
  (`ErrorInfo`: `category`, `system`, `retryable`, `message`, `hint`), left out
  of rows that did not fail and of tables. Put the human text in `detail`;
  never declare your own `error` field.
- Base check results on `CheckRecord`, with `status` set to `pass`, `warn`,
  `fail` or `error`.
- Type timestamps as `UtcTimestamp` and name them `<event>_at`. They render
  as `2026-01-02T03:04:05Z`.
- Use native booleans, `null` and lists in records. Do not use glyphs such as
  `✓` or `—` as data; for a table, annotate the field with `TableGlyph`
  (`Annotated[bool, TableGlyph(true="✓")]`), which every other format ignores.
- `emit` gives each format the shape [Scripting](./scripting.md#output-and-pipes)
  describes: pass a sequence for a collection, even of one item, and a single
  record for a detail view.

### Enforcement

`untaped.testing.check_conventions(NAME)` runs these checks for one
capability; each capability's tests call it. Its `import-boundary` rule
allows a capability's code to import core only as `untaped.sdk`, and another
capability only as described in
[Depending on another capability](#depending-on-another-capability).
`# untaped: allow <rule>` on the flagged node's first line allows that one
violation. It does not cover the
default-table-columns rule (see Output records); this repo's own test suite
enforces that one, and `# untaped: allow` does not apply to it.

## Stable helper surface

[`sdk.py`](../packages/untaped/src/untaped/sdk.py) lists every export,
and each helper's docstring is its reference.
[Conventions](#conventions) says which helper each rule uses. Beyond those:

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

## Piping

`--format pipe` writes the v1 envelope, one JSON object per line (see
[Pipes and record kinds](./scripting.md#envelope-format)):

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

## Packaged skills

A capability ships its agent skill as a directory holding `SKILL.md`. Declare
it on
`CapabilitySpec.skills`; the root lists and installs every composed
capability's skills (see [Agent skills](./getting-started.md#agent-skills)):

```bash
untaped skills list
untaped skills install acme --target codex
untaped skills install --all --target all
```

`SkillAsset.name` is the skill's full ID (`untaped-acme`; see
[Install skills](./getting-started.md#install-skills)). There is no core skill; capability-specific guidance
belongs in the capability's skill.

Start from this template. Copy it to
`src/<package>/skills/untaped-<capability>/SKILL.md`, replace every
UPPER_CASE placeholder, and declare it in `CapabilitySpec.skills`:

```markdown
---
name: untaped-CAPABILITY
description: Operates SYSTEM through the `untaped CAPABILITY` command (TASKS IN A FEW WORDS). Use when the user wants to INTENT, or mentions TRIGGER WORDS.
---

# untaped CAPABILITY

## When to use

One or two sentences: the job this capability does, and when another
capability or tool fits better.

## Setup

Settings live under `profiles.<name>.CAPABILITY`. Set the token with
`untaped config set CAPABILITY.token --prompt` and check the connection with
`untaped CAPABILITY whoami`. Never print, echo or log tokens.

## Commands

| When you need to | Run |
|---|---|
| CONDITION | `untaped CAPABILITY NOUN list` |
| CONDITION | `untaped CAPABILITY NOUN get NAME` |
| CONDITION, after a preview | `untaped CAPABILITY NOUN delete NAME --dry-run` |

## Workflows

1. STEP, ending on something the agent can check.
2. Preview the change with `--dry-run` and show the user what it will touch.
3. After the user approves, rerun with `--yes`. Exit 1 with
   `cancelled; no changes made` means declined; other codes mean it failed.

Read `--format json` rather than table output. Exit codes: 0 success, 1
failure or declined, 2 usage (including a write without a terminal and
without `--yes`), 3 predicate hit, 4 fix the environment, 5 retry later.

## Pitfalls

- A LIMIT, SURPRISING DEFAULT OR COMMON MISTAKE, with the reason.

## References

- Read `references/TOPIC.md` when SITUATION.
```

Composition requires only a non-empty skill name and description; the rest
of this section is guidance. The principle: say only what the agent cannot
learn from the installed CLI, and make the risky paths hard to get wrong.
`--help` and `--columns ?` answer flags and fields; the skill says which
commands form a workflow, which order is safe, what the output means and what
to do next.

Frontmatter:

- `description` equals `SkillAsset.description` exactly. It routes rather
  than instructs: in the third person, what the skill covers, then the
  intents that should load it. Keep it under 60 words, on this capability's
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

The first-party capabilities' tests (`tests/repo/test_skill_files.py`) check
that the description matches the frontmatter, that every quoted command
parses, and that no link leaves the skill.

## Managed state

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

## How composition works

`untaped` is one distribution with one executable: `untaped --version` prints
the installed `untaped` distribution's version, and a provider adds commands
under `untaped <capability>` rather than a console script of its own. At
startup the root composes the capabilities in this order:

1. **Discovery.** Every entry point in the `untaped.capabilities` group is a
   candidate, first-party ones included. Its distribution's `untaped`
   requirement is checked against the running version.
2. **Resolution.** The entry point is loaded and called, and must return a
   `CapabilitySpec`.
3. **Validation.** The declaration is checked: reserved root names, the
   entry-point name, duplicate names, sections, skills and doctor checks,
   and overlapping profile and state fields.
4. **Commit.** The survivors are mounted under their names: lazily when the
   spec has `help`, otherwise by calling the app factory now.

Every violation quarantines that provider and composition continues: a
warning names it, `untaped capabilities` lists it as quarantined, and
`untaped doctor` shows the reason. The root owns configuration, profiles,
themes and the management commands, and aggregates every capability's skills
and doctor checks.

When two providers claim the same capability name or config section, all of
them are quarantined and a warning names every claimant: no provider can take
over another's commands or settings, and the result does not depend on install
order. Uninstall one to restore the other. A capability whose settings import
another capability's `api` (ansible imports github's) is quarantined with it
when that import fails.

### Depending on another capability

A capability may import another only through that capability's public
module, `<package>.api` (for example `untaped_github.api`), never its other
internals, and only when its distribution depends on the other's (a
dependency under an extra does not count). Dependencies are one-way, and
imports of another capability stay lazy on CLI paths; a settings model that
validates against the other capability may import it at module top. An `api`
module keeps a closed `__all__`. Logic two capabilities need lives in exactly
one owner's `api` module, never forked into both.

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
see [Enforcement](#enforcement).

Test the provider callable and `SPEC.app_factory()` in isolation, assert that
the entry-point name matches `SPEC.name`, and exercise root config, profile,
skill, pipe and error paths. `untaped capabilities`, `untaped acme --help` and
`untaped doctor` show the composed surface and any quarantine reason: a
malformed provider is quarantined so the other capabilities still boot.

## SDK stability

`untaped.sdk` and `untaped.testing` are stable within a major release: a
minor or patch release adds to them and never breaks them.
Providers import only `untaped.sdk`; other `untaped` modules are internal.
For what users can rely on, see the README's
[Versioning](../README.md#versioning) section.
