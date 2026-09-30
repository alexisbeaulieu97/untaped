# Command and output conventions

Every `untaped` command, built-in or external, should look and behave the same
way. This page lists the rules and the `untaped.capability_api` helper that
implements each one. Use the helper instead of writing your own version.

## Exit codes

| Code | Meaning | How to produce it |
|---|---|---|
| 0 | Success | Return normally. |
| 1 | The thing ran and failed, a failed item, a missing name, rejected input, or a declined confirmation | Raise an `UntapedError` whose category is `failed`, `not_found`, `invalid` or `conflict` inside `report_errors()`, or call `finish(any_failed)`. |
| 2 | Usage error, found before any side effect | Raise `UsageError` inside `report_errors()`, or call `raise_usage()` outside it. |
| 3 | Predicate hit (`--check` drift, `--fail-on-match`, `--strict`) | Call `finish(any_failed, predicate_hit=True)`. |
| 4 | The environment needs fixing: config, a rejected token, missing permission | Raise with category `config` (`ConfigError`), `auth` or `permission`. |
| 5 | Temporary: network, timeout, 5xx, 429, a busy lock | Raise with category `unavailable` (`HttpTransportError` and 429/5xx statuses already are). |
| 130 | Interrupted with Ctrl-C, including at a prompt | Handled by the root shell. |

`ExitCode` names these values. Output into a closed pipe (`untaped … | head`)
exits 0 quietly, for `--help` and data commands alike.

Usage errors include conflicting flags, a value out of range, no selection,
and "requires `--yes` when not interactive". Problems that depend on
configuration or remote state are another `UntapedError`.

### Raise with a category, or inherit one

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
- A run exits with the most severe failure it saw: `130 > 2 > 4 > 5 > 1 > 3 > 0`.
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

See [exit codes](./reference/exit-codes.md) for the category table.

## Messages (stderr)

stdout carries data only. Everything else goes to stderr. With `--format
json|yaml|pipe` (or `UNTAPED_DIAGNOSTICS=json`) every stderr line below is a
JSON object instead (see [stderr diagnostics](./reference/pipes.md#stderr-diagnostics));
the helpers do this for you, so never print a JSON line yourself.

| Message | Shape | Helper |
|---|---|---|
| Error | `error: <msg>`: lowercase, no trailing period | Raise an `UntapedError`; `report_errors()` prints it. |
| Per-item error | `error: <item>: <msg>` | `resolve_each`, `batch_apply`, `report_error(exc, item=…)` |
| Not found | `<noun> not found: 'x'; known: a, b` | `not_found("profile", name, known=names)` |
| Quoted name | `'name'` | `q(name)` |
| Count | `3 repos`, never `repo(s)` | `plural(3, "repo")` |
| Hint | ``hint: run `untaped …` `` | `hint("config set awx.token --prompt")` |
| Warning | `warning: …` | `ui.message("warning", text)` |
| Success | Muted by `-q` | `ui.success(text)` |
| Summary | `<op>: 2 cloned, 1 failed` | `summary("sync", counts)` |
| Decline | `cancelled; no changes made` (exit 1) | `raise OperationCancelledError`, or `finish(outcome)` after `batch_apply` |
| Empty list | `No <plural> found.`, in table format only | `emit(rows, …, empty="No repos found.")` |
| Styled line | A Rich `Text` line (live job events), ANSI only on a terminal; `tail=` text follows it verbatim (unwrapped) | `ui.styled(text)` for stdout, `ui.styled(text, err=True)` for stderr |

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
`untaped` root, so test them with `build_root_app()`.

Command names are kebab-case. Use plural nouns for collections. Leaf verbs
come from a closed set:

- Read: `list`, `get`, `status`, `whoami`, `ping`
- Write: `create`, `set`, `unset`, `add`, `remove`, `delete`, `prune`, `edit`,
  `patch`, `apply`, `copy`, `rename`
- Update: `sync`, `refresh`
- Query: `find`, `deps`, `impact`, `graph`
- Other: `export`, `init`, `run`, `launch`, `wait`, `validate`, `test`,
  `cancel`, `relaunch`, `schema` (prints the JSON Schema of a document kind)

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
  `--yes`.
- Read identifiers with `read_identifiers(names, stdin=stdin,
  id_field="…", accept_kinds={"<cap>.<noun>"})`. A pipe record of another
  kind exits 2. Empty stdin is an error, except for commands that act on a
  filtered selection (such as `workspace foreach --stdin`), which read it with
  `read_stdin_input(allow_empty=True)`: an empty pipe then does nothing,
  reports like an empty list, and exits 0.
- Before prompting, check `ui.can_prompt` on the `UiContext` that will prompt;
  without a terminal, fail with a hint naming the flag that supplies the value.
- Commands that need whole records or a mixed input use
  `read_stdin_input(accept_kinds=…)` or `read_records(accept_kinds=…)`.
  Never read `sys.stdin` directly.

## Output records

- The record is for scripts and agents; the table is for a human scanning
  rows. Keep every field on the record and pick the table's default columns
  with `emit(rows, …, table_columns=[…])`: the identifying field, what
  changed or its state, and what the reader acts on next. Leave out fields
  that repeat the command's own arguments or are empty on most rows. Never
  pass defaults as `columns=` (`columns or DEFAULTS`): `--columns +name/-name`
  edits `table_columns`, and a named column is always shown, even when empty.
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
  `✓` or `—` as data.
- `--format json` and `--format yaml` print one document per invocation: an
  array for a collection (even when it spans several ids), a mapping for a
  single record. `pipe` and `raw` print one line per record. Only a live
  stream (`--follow`) prints json as one object per line (NDJSON).

## Enforcement

`tests/conventions/` checks these rules on every test run:

| Test | What it checks |
|---|---|
| `test_help_tree.py` | Verbs, flags and help text |
| `test_message_lint.py` | stderr wording |
| `test_structure.py` | `errors.py`, exception names, ports, config sections, private test imports |
| `test_layering.py` | Import direction inside a capability; only `cli` resolves settings |

Existing violations are listed in
`tests/conventions/baselines/<check>/<owner>.txt`. A new violation fails the
tests. A fixed violation also fails until you delete its baseline line, so the
baselines can only shrink.

## See also

- [Building a capability provider](./plugins.md)
- [Configuration](./configuration.md)
