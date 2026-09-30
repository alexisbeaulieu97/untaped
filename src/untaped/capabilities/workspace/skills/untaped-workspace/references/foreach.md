# Running a command in each repo

`foreach` runs a shell command in each selected repo's directory. The command
may change repos, so confirm with the user before running anything that
writes, commits or pushes.

## The command

- Quote it: `untaped workspace foreach WS 'make build'`. An unquoted first
  word that is not a workspace fails with a hint to quote.
- Commands get no stdin: a tool that asks a question reads end-of-input
  instead of waiting.
- Each command has 600 seconds unless `--timeout N` says otherwise; a
  command that runs out gets return code 124 and `timed out after <N>s` on
  stderr.

## Choosing repos

| Selection | When |
|---|---|
| `--repo REPO` (repeatable) | a few repos known by name or URL |
| `--stdin` | repos chosen by an earlier command, e.g. only the dirty ones |
| `--all` | every registered workspace, in registry order |

- `--stdin` reads repo names, or `workspace.repo`, `workspace.status` or
  `workspace.sync_outcome` records of the same workspace; records from
  another workspace exit 2. An empty pipe runs nothing and exits 0.
- `--stdin` cannot be combined with `--repo` or `--all`.
- Under `--all`, repos are named `<workspace>/<repo>`, `--repo` filters each
  workspace, and a workspace whose manifest cannot be read is skipped with a
  warning.

Illustration, stashing only the dirty repos of an invented workspace:

```bash
untaped workspace status acme-prod --dirty --format pipe \
  | untaped workspace foreach acme-prod 'git stash' --stdin
```

## When a repo fails

| Flag | Runs every repo | Exit code | Fits when |
|---|---|---|---|
| (none) | no: stops at the first failure | non-zero | a failure means the rest should not run |
| `--continue-on-error` | yes | non-zero if any failed | every repo's result matters and a failure must still show |
| `--ignore-errors` | yes | 0 | partial failure is acceptable (it wins over `--continue-on-error`) |

On the first failure, commands already running finish and queued ones (and
later `--all` workspaces) are cancelled. Pass `-j 1` when strict ordering
matters: with several workers, repos after the failing one may already have
run. Ctrl-C stops running commands and cancels queued ones.

## Output

- Table output prints each repo's captured stdout and stderr, prefixed
  `[<repo>]`, when that repo finishes, then `failed in: <repos>` on stderr if
  any failed. Output appears only once a repo's command exits.
- `--format json` (or yaml, raw, pipe) emits one `workspace.foreach_outcome`
  row per repo, including `returncode`, `stdout` and `stderr`; read these
  rather than the table.
