# Capability conventions

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
- `undeclared-write`, `mutation-format` and `destructive-controls`: the
  write rules under [Options](#options).

It does not check state writes or an error's category. Composition, not
`check_conventions`, quarantines a provider whose skill name or doctor-check
ID duplicates another's. The message and option rules below have their own
checks, named in each violation line.

## Exit codes

[Exit codes](./exit-codes.md) defines what each code means. To
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

### Raise with a category, or inherit one

Every `UntapedError` has a `category` (`ErrorCategory`) that selects the exit
code, and a `system` that says who is responsible (`untaped`, `local`, `git`,
or the service section, such as `awx`); [Scripting](./exit-codes.md#categories)
lists the categories. A capability's error classes declare them as class defaults
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
  [precedence](./exit-codes.md#precedence)).
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

See [exit codes](./exit-codes.md) for the category table.

## Messages (stderr)

stdout carries data only. Everything else goes to stderr. With `--format
json|yaml|pipe` (or `UNTAPED_DIAGNOSTICS=json`) every stderr line below is a
JSON object instead (see [stderr diagnostics](../scripting.md#stderr-diagnostics));
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

## Options

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
`untaped` root, so test them with `untaped.testing.invoke_root([...])`.

Command names are kebab-case; use plural nouns for collections. Names are
free, but a command that writes declares it with `@writes`, or
`@writes(destructive=True)` when it deletes or overwrites data:

- A command exposing `--yes` or `--dry-run` must be declared
  (`undeclared-write`).
- A declared write takes `--format` (`mutation-format`).
- A destructive command takes both `--yes` and `--dry-run`
  (`destructive-controls`).

## Confirmation and stdin

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
- Read stdin only through the helpers in [Piping](#piping)
  (`read_identifiers`, `read_stdin_input`, `read_records`), never
  `sys.stdin` directly; that section also covers empty pipes.
- Before prompting, check `ui.can_prompt` on the `UiContext` that will prompt;
  without a terminal, fail with a hint naming the flag that supplies the value.

## Output records

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
  (`ErrorInfo`, whose fields [Scripting](../scripting.md#failed-rows-the-error-field)
  lists), left out of rows that did not fail and of tables. Put the human text in `detail`;
  never declare your own `error` field.
- Base check results on `CheckRecord`, with `status` set to `pass`, `warn`,
  `fail` or `error`.
- Type timestamps as `UtcTimestamp` and name them `<event>_at`. They render
  as `2026-01-02T03:04:05Z`.
- Use native booleans, `null` and lists in records. Do not use glyphs such as
  `✓` or `—` as data; for a table, annotate the field with `TableGlyph`
  (`Annotated[bool, TableGlyph(true="✓")]`), which every other format ignores.
- `emit` gives each format the shape [Scripting](../scripting.md#output-and-pipes)
  describes: pass a sequence for a collection, even of one item, and a single
  record for a detail view.

## Enforcement

`untaped.testing.check_conventions(NAME)` runs these checks for one
capability; each capability's tests call it. Its `import-boundary` rule
enforces the import limits in the introduction and in
[Depending on another capability](../composition.md#depending-on-another-capability).
`# untaped: allow <rule>` on the flagged node's first line allows that one
violation. It does not apply to the default-table-columns rule (see Output
records).

## Stable helper surface

[`sdk.py`](../../packages/untaped/src/untaped/sdk.py) lists every export,
and each helper's docstring is its reference.
[Conventions](./conventions.md) says which helper each rule uses. Beyond those:

- Read settings with `app_context().section(name, Model)` or
  `get_config_section(name, Model)`, which validate only your section, rather
  than `app_context().settings`.
- For a token, declare `token_sources: ClassVar[TokenSources] =
  TokenSources(env=(...))` and a `token_command: TokenCommand = None` field
  beside `token` on your profile model; see [Tokens](../configuration.md#tokens).
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
[Scripting](../scripting.md#envelope-format)):

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
values or the parsed envelopes (a `StdinInput`); `read_records(accept_kinds=...)`
returns only envelopes. All raise on empty stdin.
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

## Managed state

A capability that writes structured state declares a disjoint `state_model`
and writes through the state helpers:

```python
from untaped.sdk import StateCollection

_items = StateCollection("acme", "items", id_field="id")
_items.upsert({"id": "one", "label": "Example"})
```

State lives in the [state file](../configuration.md#file-and-layout), outside
profiles, and is never a user setting. The helpers keep other capabilities'
sections intact under the shared lock, so never read or write either file
directly.

## SDK stability

`untaped.sdk` and `untaped.testing` are stable within a major release: a
minor or patch release adds to them and never breaks them.
Providers import only `untaped.sdk`, plus `untaped.testing` in tests; other
`untaped` modules are internal.
For what users can rely on, see [Versioning](../versioning.md).
