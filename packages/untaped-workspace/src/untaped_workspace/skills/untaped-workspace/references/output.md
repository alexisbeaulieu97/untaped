# Workspace output

Every row is a record with an absolute `target_path`: the workspace
directory for `workspace.workspace`, the repo directory for the others.
`--columns '?'` on `list` or `status` lists the fields of a table.

## Record kinds

| Command | Kind | Read |
|---|---|---|
| `list` | `workspace.workspace` | `repos` counts the repos; `archived_at` is null while active |
| `create`, `add` | `workspace.repo_outcome` | `action`: `created`, `checked_out`, `unchanged`, `failed` |
| `status` | `workspace.status` | `state`: `ok`, `missing`, `cache_missing` (the repo is missing from the repo store), `error`; `upstream` is null until the branch is on origin; `blockers` lists why the repo blocks archive (empty when it does not) |
| `archive` | `workspace.archive_outcome` | `action`: `removed`, `planned`, `skipped`, `failed`; a last row with an empty `repo` is the workspace directory (`skipped` when other files stay in it) |
| `remove` | `workspace.remove_outcome` | `action`: `released`, `removed`, `kept`, `skipped`, `failed`, `planned`; `freed_bytes` is the space a `removed` repo took; a last row with an empty `repo` is the workspace (`removed` once its records are gone) |
| `run` | `workspace.run_outcome` | `action`: `ran`, `failed`, `skipped`; `returncode` is null for a timeout or a repo not run, negative for a signal |

`status --fetch` fetches each repo first (status is otherwise offline); a
repo whose fetch fails keeps its local state, with `detail` "fetch failed:
..." and an error; one whose history backfill stopped after a good fetch
keeps `detail` "history backfill stopped: ...", with no error. A repo whose
git state cannot be read is an `error` row
with an `error` field. `status --all` covers every active workspace.

`create` and `add` in table format end with the workspace path as the last
stdout line; `-q` prints only the path. Other formats print records only.

## Piping

Pipe repo records to `create` or `add` with `--stdin`: `workspace.repo`, or
any kind a repo provider reads (`github.repo`, `github.sweep_repo`,
`github.corpus_repo`). The plugin whose record it is turns it into a repo
without an API call (GitHub applies `github.git_protocol`); a record without a
kind, or of a kind no provider reads, exits 2:

```bash
untaped github repos list --team acme/platform --format pipe | untaped workspace create NAME --stdin
```

`repos resolve` writes `workspace.resolve_outcome` rows: `updated` (with
`detail` "was OLD-URL"), `unchanged`, `skipped` (a typed URL, or its provider
not installed or ready) or `failed`.

`run --stdin` takes `status`, `create`/`add` or `run` rows (or repo
names) to choose the repos it runs in; see
[run.md](run.md#choosing-repos).

`untaped recipe apply --stdin` reads `target_path` from `status` or
`create` rows:

```bash
untaped workspace status NAME --format pipe | untaped recipe apply acme/editorconfig --stdin --dry-run
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A `failed` row from `create`, `add`, `remove` or `run` (see [run.md](run.md#failures)); archive or remove refused (blocked repos, unpushed branches); a `status --fetch` failure or `error` row; an unknown workspace name; `create` on a name whose directory already holds files |
| 2 | Usage error, including no NAME outside a workspace, an unknown repo, or `archive --force` or `remove` without a terminal and without `--yes` |
| 3 | `status --check` and some repo would block archive |
| 4, 5 | By failure category: fix the setup (4), try again later (5) |
