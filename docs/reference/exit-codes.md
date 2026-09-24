# Exit codes

Every `untaped` command uses the same exit codes. Scripts can rely on them.

| Code | Meaning |
|---|---|
| 0 | Success. Per-item `skipped` rows are success too. |
| 1 | Runtime failure, at least one failed item, or a declined confirmation (`cancelled; no changes made`). |
| 2 | Usage error, found before any side effect: an unknown flag, conflicting flags, a value out of range, no selection, or a prompt with no terminal and no `--yes`. |
| 3 | Predicate hit: the command worked, and the condition you asked it to check was true. |
| 130 | Interrupted with Ctrl-C, including at a prompt. |

A batch command that fails on some items still prints a row for every item,
then exits 1. When both a failure and a predicate hit happen, the exit code
is 1.

## Commands that exit 3

| Command | Exits 3 when |
|---|---|
| `untaped github sweep --fail-on-match` | Any repository matched the query. |
| `untaped github sweep --strict` | Any repository could not be scanned. |
| `untaped recipe apply --check` | Any target would change. |

Use these in CI to tell "the check found something" (3) apart from "the tool
failed" (1):

```bash
untaped github sweep --org acme --grep 'log4j' --fail-on-match --format raw --columns full_name
status=$?
if [ "$status" -eq 3 ]; then echo "banned pattern found"; fi
```

## Codes inside records

Some rows carry a code of their own. It never becomes the process exit code:

- `untaped workspace foreach` rows have a `returncode` for each repo's
  command; `124` means the command timed out (`--timeout`). The process then
  exits 1 if any repo failed, or 0 with `--ignore-errors`.
- `untaped awx test run` reports each case's result in its rows; the command
  exits 1 if any case failed or errored.

## See also

- [Command and output conventions](../conventions.md#exit-codes): how
  capability code produces these codes.
- [Pipes and record kinds](./pipes.md)
