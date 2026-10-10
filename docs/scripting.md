# Scripting

How to combine `untaped` commands in scripts and CI: pipes, structured
output and stderr diagnostics. The reference pages list the
[output records](./reference/records.md), [exit codes](./reference/exit-codes.md)
and [environment variables](./reference/environment.md);
[Versioning](./versioning.md) says what stays stable within a major release.

## Output and pipes

`--format pipe` writes records that another `untaped` command reads with
`--stdin`. Each record keeps all its fields and names its kind, so the
consumer never parses table text.

```bash
untaped github repos list --org acme --format pipe \
  | untaped workspace create acme --stdin
```

`--format json` and `--format yaml` print one document per invocation: an
array for a collection (even when it spans several ids), a mapping for a
single record. `pipe` and `raw` print one line per record. Only a live
stream (`--follow`) prints json as one object per line (NDJSON).

### Envelope format

`--format pipe` writes NDJSON: one JSON object per line.

```json
{"untaped": "1", "kind": "github.repo", "record": {"full_name": "acme/api", "...": "..."}}
```

| Field | Meaning |
|---|---|
| `untaped` | Envelope version. Always `"1"`. A consumer rejects other versions. |
| `kind` | Record kind, `<plugin>.<noun>` (root commands use `untaped.<noun>`). May be `null`. |
| `record` | The row, as a JSON object. Values are JSON types; timestamps are UTC strings such as `2026-01-02T03:04:05Z`, with microseconds when the time has them (`2026-01-02T03:04:05.250000Z`). |

Because every line stands alone, `head`, `grep` and `cat a b` keep a stream
valid.

A record that names a concrete filesystem target carries it as an absolute,
non-empty `record.target_path`. A consumer reads this field rather than
another plugin's domain fields.

### How `--stdin` reads input

- The first non-blank line decides the mode. If it is an envelope, every line
  must be one. Otherwise every line is a bare value (a name, an ID, a path).
  Mixing the two is an error.
- A consumer lists the kinds it accepts. A record of another kind exits 2
  (`line N: record kind 'awx.host' is not accepted here; expected …`). A
  record whose `kind` is `null` is accepted.
- Kinds ending in `.summary` (`<plugin>.<noun>.summary`) are summary
  rows, not items. `recipe apply --stdin` skips them.
- Empty stdin is an error (`no identifiers received on stdin`), except for
  `workspace run --stdin`, where it selects no repos.
- `--stdin` and positional arguments cannot be combined (exit 2).
- When stdin carries data, confirmation prompts read the terminal
  (`/dev/tty`). With no terminal, pass `--yes` (or `--dry-run`).

### Failed rows: the `error` field

A failed row of an outcome record (`*_outcome` kinds such as
`workspace.repo_outcome`, and per-repo records such as `workspace.status`)
carries an `error` object next to its human `detail`. Rows that did not fail
have no `error` key.

```json
{"name": "Deploy", "action": "failed", "detail": "HTTP 503 for https://aap/api/v2/job_templates/7/", "error": {"category": "unavailable", "system": "awx", "retryable": true, "message": "HTTP 503 for https://aap/api/v2/job_templates/7/", "hint": null}}
```

| Field | Meaning |
|---|---|
| `category` | What kind of failure it is; it selects the exit code. See [categories](./reference/exit-codes.md#categories). |
| `system` | Who is responsible; see [categories](./reference/exit-codes.md#categories). |
| `retryable` | `true` only for `unavailable` failures. |
| `message` | The failure, as `detail` shows it. |
| `hint` | A follow-up such as ``run `untaped auth set awx` ``, or `null`. |

Tables leave `error` out (the `detail` column says the same); ask for it with
`--columns error` or use `json`, `yaml` or `pipe`.

URL passwords are masked in every message, detail and record. `error` is a
reserved record field.

### stderr diagnostics

stdout carries data only. With `--format json`, `yaml` or `pipe`, stderr
carries JSON Lines: one object per error, per-item error, warning, hint or
info message. Progress spinners are silent in this mode.

- The format counts whether it comes from the flag, `UNTAPED_FORMAT` or
  `ui.format`. A command's own default, such as `export`'s YAML, does not.
- `UNTAPED_DIAGNOSTICS=json` turns JSON Lines on for any format;
  `UNTAPED_DIAGNOSTICS=text` keeps text lines.
- A parse error (an unknown flag or command) and a warning about a
  quarantined provider come before `ui.format` is read, so only a `--format`
  on the command line or in `UNTAPED_FORMAT` switches them.

```json
{"level": "error", "message": "AWX rejected the token (HTTP 401)", "category": "auth", "system": "awx", "retryable": false, "hint": "run `untaped auth set awx`", "exit_code": 4, "details": {"status": 401, "url": "https://aap/api/v2/me/", "attempts": 1}}
{"level": "error", "item": "Deploy", "message": "HTTP 503 for https://aap/api/v2/job_templates/7/", "category": "unavailable", "system": "awx", "retryable": true, "hint": null, "exit_code": 5, "details": {"status": 503, "url": "https://aap/api/v2/job_templates/7/", "attempts": 3}}
{"level": "warning", "message": "--parallel 64 clamped to 16 (2 * os.cpu_count())"}
{"level": "hint", "message": "run `untaped skills install`"}
{"level": "info", "message": "sync: 2 cloned, 1 failed"}
```

| Field | On | Meaning |
|---|---|---|
| `level` | every line | `error`, `warning`, `hint`, `info`, `success`, or `debug` (with `--verbose`) |
| `message` | every line | The text the line would show, without its `error:`/`warning:`/`hint:` prefix |
| `item` | per-item errors | The item that failed (a name, an ID) |
| `category`, `system`, `retryable`, `hint`, `exit_code`, `details` | errors | As in the `error` field above; `exit_code` is the code this failure alone selects, and `details` holds machine context such as `status`, `url` and `attempts` |

An `error` line that does not come from a raised failure (a plain
`error: …` message) carries only `level`, `message` and `hint`; the exit code
still follows the rules in [exit codes](./reference/exit-codes.md#precedence).

## In CI

The [commands that exit 3](./reference/exit-codes.md#commands-that-exit-3)
let a CI step tell "the check found something" (3) apart from "the tool
failed" (1), "fix the setup" (4) and "try again later" (5):

```bash
untaped github sweep --org acme --grep 'log4j' --fail-on-match --format raw --columns repo
case $? in
  0) echo "clean" ;;
  3) echo "banned pattern found" ;;
  4) echo "fix the token or config" ;;
  5) echo "GitHub unavailable; retry later" ;;
  *) echo "sweep failed" ;;
esac
```

## See also

- [Output records](./reference/conventions.md#output-records) and
  [Piping](./reference/conventions.md#piping): record field rules and the
  pipe helpers for plugin authors.
