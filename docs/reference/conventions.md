# Plugin conventions

Every `untaped` command, first-party or third-party, looks and behaves the same
way. Each rule below names the `untaped.sdk` helper that implements it; use
the helper rather than your own version.

A plugin also follows these rules:

- A plugin imports only `untaped.sdk`, [`untaped.sdk.contracts`](../contracts.md)
  and `untaped.sdk.testing` from `untaped`, and another plugin only through its
  `api` module, declared (see
  [Depending on another plugin](#depending-on-another-plugin)).
- A plugin reads and writes only its own config section, state, skills and doctor checks.
- Layering is `cli → application → domain`, with `infrastructure → domain`
  (and, for typing only, the ports it implements); `domain` imports no layer.
- Errors raise with a category and a system (see
  [Raise with a category](#raise-with-a-category-or-inherit-one)).
- Commands that ask before they write declare `confirm: Confirm`, which
  gives them `--yes` and `--dry-run`; one that writes without asking takes
  `dry_run: DryRun` (see [Options](#options)).

[`untaped lint`](#enforcement) flags part of this:

- `plugin-name`: a distribution or import package not named after the plugin;
- `layer`: an import against the layer direction, and `settings`: settings
  resolved outside `cli`;
- `errors-module`, `exception-base` and `error-system`: no `errors.py`, an
  exception that is not an `UntapedError`, or a plugin's base error
  class without a `system`.

It does not check state writes or an error's category. Composition, not
`untaped lint`, refuses a command class that breaks the option rules below,
naming the rule and the field. The message rules have their own checks,
named in each violation line.

## Exit codes

[Exit codes](./exit-codes.md) defines what each code means. To produce one:

- 0: return normally.
- 1: raise an `UntapedError` (category `failed`, `not_found`, `invalid` or
  `conflict`), yield a row whose `action` failed, or raise `Failed()` after
  the rows.
- 2: raise `UsageError(message)`; outside a command (a validator, option
  parsing), call `raise_usage(message)`.
- 3: raise `Matched()`.
- 4: raise with category `config` (`ConfigError`), `auth` or `permission`.
- 5: raise with category `unavailable` (`HttpTransportError` and 429/5xx
  statuses already are).
- 130: handled by the root.

`ExitCode` names these values.

Usage errors include conflicting flags, a value out of range, no selection,
and "requires `--yes` when not interactive". Problems that depend on
configuration or remote state are another `UntapedError`.

### Raise with a category, or inherit one

Every `UntapedError` has a `category` (`ErrorCategory`) that selects the exit
code, and a `system` that says who is responsible (`untaped`, `local`, `git`,
or the service section, such as `awx`); [Scripting](./exit-codes.md#categories)
lists the categories. A plugin's error classes declare them as class defaults
(`category = ErrorCategory.NOT_FOUND`, `system = "awx"`); pass
`category=`, `system=`, `hint=` or `details=` to override one instance.

- `ConfigError` means **local setup** (settings, credentials, a missing tool)
  and exits 4. An invalid input *file* the command reads, or a value the user
  gave that fails validation, is `invalid` (exit 1), not config.
- HTTP errors take their category from the status and their `system` from
  the plugin whose settings built the client
  (`self.ctx.http.client(...)`). A mapper that turns them into plugin
  errors keeps both: `JiraApiError(msg, **attribution(err))`, and a 401 stays
  `auth` even when it becomes a `ConfigError` for its hint.
- Put a follow-up command in `hint=` rather than in the message, named by its
  class: `hint=Login.spelled("awx")` reads ``run `untaped auth login awx` ``
  (core's commands come from `untaped.core.api`), printed as a `hint:` line.
  Only a run spells a class, so a validator refuses with
  ``InvalidValue("use `{}`", Login.spelled())``, spelled when it is reported.
- When a new error replaces a caught one, pass `**attribution(exc)` so the
  category, system, hint and details survive.
- A run exits with the most severe failure it printed or emitted as a row
  ([precedence](./exit-codes.md#precedence)), even when it returns normally.
- A failed row carries the structured failure next to its `detail`:
  `error=Failure.from_exception(exc, message=detail)` on `OutcomeRecord` and
  `TargetRecord` rows (`.model_dump(mode="json")` for a dict row). Building
  the `Failure` counts nothing; printing the row counts it. Keep the exception
  until the row is built; never flatten it into a string first.

See [exit codes](./exit-codes.md) for the category table.

## Messages (stderr)

stdout carries data only. Everything else goes to stderr. With `--format
json|yaml|pipe` (or `UNTAPED_DIAGNOSTICS=json`) every stderr line below is a
JSON object instead (see [stderr diagnostics](../scripting.md#stderr-diagnostics));
the helpers do this for you, so never print a JSON line yourself.

| Message | Shape | Helper |
|---|---|---|
| Error | `error: <msg>`: lowercase, no trailing period | Raise an `UntapedError`; core prints it. |
| Per-item error | `error: <item>: <msg>` | `ctx.diag.failure(exc, item=…)` (a failed row: `row.error`) |
| Not found | `<noun> not found: 'x'; known: a, b` | `not_found("profile", name, known=names)` |
| Quoted name | `'name'` | `q(name)` |
| Count | `3 repos`, never `repo(s)` | `plural(3, "repo")` |
| Hint | ``hint: run `untaped …` `` (or a short instruction that is not a command, like the bare-install hint) | `ctx.diag.hint(run_hint(Login.spelled("awx")))`; `hint(…)` ends an error's message; `command_line(…)` quotes one inline |
| Warning | `warning: …` | `ctx.diag.warning(text)` |
| Success | Muted by `-q` | `ctx.diag.success(text)` |
| Summary | `<op>: 2 cloned, 1 failed` | `summary("sync", counts)` |
| Decline | `cancelled; no changes made` (exit 1) | A no to `self.confirm(...)`, or `raise OperationCancelledError` |
| Empty list | `No <plural> found.`, in table format only | `self.output.empty("No repos found.")` |
| Progress | `<label> · <phase> 3/12 · <text> (4s)`, composed by the sink: the label is a noun phrase without `…`; report counts, never a formatted fraction | `with ctx.diag.progress(label) as p:` then `p.update(text, phase=, done=, total=)`; an inner layer takes `progress: ProgressHandle = NO_PROGRESS` |
| Styled line | A Rich `Text` line for streamed human output (live job events) | stdout: return the `Text` from an `Output` renderer (core prints it, in colour on a terminal); stderr: `ctx.diag.info(text, unmuted=True)`, or `ctx.diag.relay(text)` for another tool's output |
| Preview table | Records a user reads on stderr before a question (planned changes) | `ctx.diag.info(rows, unmuted=True)`, or rows in a `preview=`: core lays them out as a stdout table, `Column` fields in the run's theme; never lay out a table or read the theme yourself |

`ctx.diag` writes every stderr line; never print an `error:`, `warning:`
or `failed:` line. `-q` mutes `info`, `success` and progress; `unmuted=True` is for a preview before
a prompt, a stream the user asked to watch or output a flag asks for. Never `print()`, `warnings.warn()`
(take a `warn` callback; a deprecated Python API warns through its [mark](deprecation.md)) or your own `Console`.

**Typed diagnostics.** Each stderr line has a `kind`. To add one, subclass
`Message` or `Failure` (add fields, never levels), list it in your plugin
class's `records` and emit it with `self.ctx.diag.emit(...)`. Relay another
tool's output (a hook's stderr, a job log) with `diag.relay(text)`: it never
counts.

**Threads.** `ctx.diag` writes to the run in the thread's context: start
workers with `bounded_map` or `Parallel.map`, or run your own thread's target
in `contextvars.copy_context().run`, or its lines skip the run and never count.

## Options

Declare a shared field block instead of a flag of your own:

| Field | Flags |
|---|---|
| `output: Output[R]` | `-f/--format` (default `table`, or the user's `UNTAPED_FORMAT`/`untaped.ui.format`; the formats its `Output(formats=)` names, the first the default) and `-c/--columns` |
| `confirm: Confirm` | `-y/--yes`, which skips only the prompt, and `--dry-run`, which still wins |
| `dry_run: DryRun` | `--dry-run`: preview, then exit 0 |
| `stdin: Stdin[R]` | `--stdin` |
| `refresh: Refresh` | `--refresh` and `--cached` |
| `parallel: Parallel` | `-j/--parallel N`: N >= 1, clamped to its `cap` |
| `limit: Limit` | `--limit N`: N >= 1 |

These short flags are reserved and have one meaning each: `-f --format`,
`-c --columns`, `-y --yes`, `-j --parallel`, `-o --out`, `-i --ignore-case`,
`-r --repo`.

Rules for fields, which composition checks:

- A field is an option unless it is marked `Positional`; arguments fill in
  field order, and none follows an optional or a list one.
- Every field has help text: `Field(description=...)`.
- A boolean that defaults to true, or a `bool | None`, turns off with
  `--no-*`; no other option has a negative. A list option takes one value
  per flag.
- An argument can't be deprecated: deprecate an option, or the command.

To rename a command, keep the old spelling as a hidden alias on its class,
`aliases = (Deprecated(replacement="me", removed_in=12),)`;
[Deprecating something](deprecation.md) covers every kind.

Command names are kebab-case; use plural nouns for collections. A command
that asks before it writes declares `confirm: Confirm`, which gives it `--yes`
and `--dry-run`; one that writes without asking takes `dry_run: DryRun`; one
that prints records declares `Output[R]`, which gives it `--format`.

## Confirmation and stdin

- A command that asks before it writes declares `confirm: Confirm` and asks
  `self.confirm(question, preview=...)`: `--yes` answers it, `--dry-run`
  previews only, and a run with no terminal exits 2 naming `--yes`.
- When stdin carries piped data, prompts read from the controlling terminal
  (`/dev/tty`). If there is no terminal, the command exits 2 and names
  `--yes`. In tests, `untaped.sdk.testing.invoke_cli(..., terminal=True,
  prompt_backend=...)` simulates that terminal; without `terminal=True` there
  is none.
- Read stdin only through a `Stdin[R]` field ([Piping](#piping)), never
  `sys.stdin` directly; that section also covers empty pipes.
- Before prompting, check `self.ctx.terminal.can_prompt`;
  without a terminal, fail with a hint naming the flag that supplies the value.

## Output records

- The record is for scripts and agents; the table is for a human scanning
  rows. Keep every field on the record and mark the table's default columns
  on its fields, `name: Annotated[str, Column]`: the identifying field, what
  changed or its state, and what the reader acts on next. Leave out fields
  that repeat the command's own arguments or are empty on most rows.
- The columns come in the order json shows the fields. An outcome's `action`
  and a check's `status` are marked by their base and join the record's own
  marks; a record marking none of its own fields shows every field. Redeclare
  an inherited field to mark it. `Output(columns=(key(Repo).full_name, …))`
  sets one command's defaults; a single record shows every field. `run`
  changes them with `self.output.columns.add`, `.drop` and `.show`. A column
  is a field reference; a string names a key no field declares (a `Spread`
  key, or one an `extra="allow"` row carries).
- `--columns +name/-name` edits the default columns, and a named column is
  always shown, even when empty. Core refuses a name `R` has no field for
  before `run` starts.
- A record's kind is the plugin whose package defines it and its class in snake case
  (`acme-tools.widget` for `Widget`), given at composition: name a noun, `<Verb>Outcome` for a
  mutation. One model per kind; core reads it from the rows. Elsewhere name the model
  (`kind_of(Widget)`), never the string, so a rename misses no line. Fields must survive a JSON round trip (`Record`'s docstring says which
  fail). Records refuse undeclared fields; `extra="ignore"`
  keeps a service answer's typed part, `"allow"` all of it (extras `JsonValue`, base fields first).
- A record no command's output names (a diagnostic, a row a stream prints) goes in your plugin
  class's `records`, which gives it the kind `<plugin>.<snake_case of the class>`.
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
  (`Failure`, whose fields [Scripting](../scripting.md#failed-rows-the-error-field)
  lists), left out of rows that did not fail and of tables. Put the human text in `detail`;
  never declare your own `error` field.
- Base check results on `CheckRecord`, with `status` set to `pass`, `warn`,
  `fail` or `error`.
- Type timestamps as `UtcTimestamp` and name them `<event>_at`. They render
  as `2026-01-02T03:04:05Z`, with microseconds outside tables. Read a time
  from a server's payload with `parse_utc`: `None` for anything not ISO 8601.
- Use native booleans, `null` and lists in records. Do not use glyphs such as
  `✓` or `—` as data; for a table, annotate the field with `TableGlyph`
  (`Annotated[bool, TableGlyph(true="✓")]`), which every other format ignores.
- `Output` gives each format the shape [Scripting](../scripting.md#output-and-pipes)
  describes: `run` yields a collection, even of one item, and returns a single
  record for a detail view.

## Enforcement

`untaped lint` runs these checks for one plugin ([Lint](../lint.md)); each
plugin's tests call `untaped_lint.testing.assert_clean(NAME)`. `import-boundary` enforces the import rules above
and in [Depending on another plugin](#depending-on-another-plugin);
`provides-requirement` the [ranges a provider declares](../plugins.md#filling-another-plugins-contract);
`terminal-boundary` bars `prompt_toolkit` (build [screens](../screens.md)); `blob-reader`
keeps repo store blob reads on `store.prefetched(...).run()`; `store-format` opens a database in the plugin's folder through a [store](../caches.md#stores); the stability rules check [marks](../plugins.md#experimental-and-deprecated-commands).
A help panel (`Group`) of your own takes a `sort_key` below 100 and no core panel's name (`unkeyed-panel`, `reserved-panel`).
`# untaped: allow <rule>` on the flagged node's first line allows one violation,
except the stability rules, the settings rules, and the command, option and panel rules.

## Depending on another plugin

A plugin may import another only through that plugin's public
module, `<package>.api` (for example `untaped_github.api`), never its other
internals, and only when its distribution depends on the other's (an owner
it fills a contract of too, since a fill subclasses it). Dependencies are one-way, and
imports of another plugin stay lazy on CLI paths; a settings model that
validates against the other plugin may import it at module top. An `api`
module keeps a closed `__all__`. Logic two plugins need lives in exactly
one owner's `api` module, never forked into both.

## Stable helper surface

[`untaped/sdk/_exports.py`](../../packages/untaped/src/untaped/sdk/_exports.py)
lists every export, and each helper's docstring is its reference.
The sections above name the helper for each rule. Beyond those:

- A command reads its plugin's settings as `self.settings`; other code in
  the run reads them with `settings_of(Model)`, which validates only that
  section. A test reads them inside `install(Plugin).running()`.
- For a service, hold its server in one `NeedsSetup`-marked `CredentialKey`
  setting and declare each secret as a `Credential` on the plugin class,
  and build its client with
  `self.ctx.http.client(self.settings, base_url=…, bearer=…)`; see
  [Credentials](../credentials.md).
- For a domain-specific HTTP or filesystem adapter the API does not export,
  use your own dependency rather than an `untaped` internal.
- To keep a repository, use `RepoStore.for_url(url, plugin=..., error=...)`
  from `untaped_git.api`, not a bare repo or a root setting of your own:
  `plugin=` takes your plugin's name or what names it (a command's
  `self.ctx.plugin`). Read blobs through
  `store.prefetched(...).run(...)` (the `blob-reader` rule).

A row-producing command declares `Output[R]` and yields its records, which
take their kinds from their classes:

```python
from collections.abc import Iterator

from untaped.sdk import Command, Output, Record


class Item(Record):
    id: str


class Items(Command):
    """List the items."""

    output: Output[Item]

    def run(self) -> Iterator[Item]:
        yield Item(id="one")
```

## Piping

`--format pipe` writes the v1 envelope, one JSON object per line (see
[Scripting](../scripting.md#envelope-format)):

```json
{"untaped": "1", "kind": "acme.item", "record": {"repo": "octocat/Hello-World"}}
```

A command that reads stdin declares `stdin: Stdin[Item]`, which adds
`--stdin` and reads bare identifiers or a pipe stream: a row of a kind not
`Item`'s exits 2 instead of being misread, and each row is validated strictly as
its kind's record. `self.stdin.identifiers(given)` gives each bare line or
row's `Key` field, `values(given)` each bare line or row, and `rows()` whole
rows only. Each raises on empty stdin. When an empty pipe (a filter that matched
nothing) should do nothing, pass `allow_empty=True`: it returns no values,
which the command must treat as "nothing to do", never as "everything". A
terminal stdin with nothing piped still raises.

A plugin joins pipelines with the first-party plugins:

```bash
untaped github search repos --format pipe | untaped acme import --stdin
```

Put a filesystem destination in an absolute `record.target_path`, so
consumers such as `recipe apply --stdin` find it without knowing the
producer. Subclassing `TargetRecord` enforces this; `OutcomeRecord` fixes the
`action` field of mutation results. When `target_path` identifies the record,
re-declare it as `target_path: AbsolutePath` so it leads the output.

## Managed state

A plugin keeps what only it holds (what users created through its commands)
as typed rows in a `StateRows` its plugin class holds
([Plugin state](../state.md)), in its own `state.yml`. The rows keep the
file's stamp and lock, so never read or write the state file directly.

A file in the plugin's folder an older release could not open is a
[store](../caches.md#stores), a `Dir(layout=N)` opened through its `open()`;
files people commit under `.untaped/` go through [the handle's
`committed`](../projects.md#a-plugins-files-in-untaped), and items projects
hold go in an [item store](../projects.md#holding-a-plugins-items).

## Settings

### Names

- A directory setting ends in `_dir`; a file setting ends in `_path`, or is a
  nested setting named `path`.
- A concurrency setting is named `parallel`, prefixed with the activity it
  bounds when it does not apply to the whole section (`git_fetch_parallel`),
  because the flag it defaults is `--parallel`.
- A duration ends in its unit: `_seconds`, `_minutes`, `_hours`, `_days` or
  `_ms`. A cache's max age is generated by its `Cache` ([Caches](../caches.md)).

`untaped lint` flags a field that breaks one of these (`settings-naming`)
and declarations that break the rules below (`settings-renames`); no marker
waives a name, so rename the field ([Renaming a setting](#renaming-a-setting)).

### Renaming a setting

Declare renames on the section's model, never a nested one, as dotted paths from it:

```python
renamed_keys: ClassVar[Mapping[str, str]] = {"sweep.sync_concurrency": "sweep.parallel"}
retired_keys: ClassVar[Mapping[str, str | Retired]] = {"cache_dir": Retired(note="use git.store_dir")}
```

- A key in `renamed_keys` (and its `UNTAPED_*` variable) is read as the new
  key, with a warning, until its removal version
  (`Renamed("new", removed_in=12)`); then it moves to `retired_keys` and is
  no longer read. Targets chain through old keys; a renamed key never
  points at a retired one; entries are never deleted, old names never reused.
  A key moving into a cache's setting is `Cache(renamed_from=...)` instead.
- A key with no successor retires as `Retired(note=...)`: doctor prints the
  note and `config migrate` deletes the key, printing the value it held.
- A rename keeps the meaning. A change of unit, type or choices is a new key;
  mark the old leaf field `Annotated[bool, deprecated(removed_in=12, replacement="mode")]`
  (outermost `Annotated`; never a model or state field). It is still read, with
  a warning naming the replacement, until then ([more](deprecation.md)).

Each warning prints once per process. Composition quarantines a plugin
whose declarations or marks break these rules (`bad-settings-keys`).

## SDK stability

`untaped.sdk`, `untaped.sdk.contracts` and `untaped.sdk.testing` are stable within a
major release: a minor or patch release adds to them and never breaks them.
Plugins import only these; other `untaped` modules are internal.
A deprecated SDK attribute or callable is removed at the major its warning
names ([Deprecating something](deprecation.md)).
For what users can rely on, see [Versioning](../versioning.md).
