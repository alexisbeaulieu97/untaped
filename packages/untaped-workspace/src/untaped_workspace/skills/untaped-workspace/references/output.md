# Workspace output

Every row is a record with an absolute `target_path`: the workspace
directory for `workspace.workspace`, the repo directory for the others.
`--columns ?` on `list` or `status` lists the fields of a table.

## Record kinds

| Command | Kind | Read |
|---|---|---|
| `list` | `workspace.workspace` | `repos` counts the repos; `archived_at` is null while active |
| `create`, `add` | `workspace.repo_outcome` | `action`: `created`, `checked_out`, `unchanged`, `failed` |
| `status` | `workspace.status` | `state`: `ok`, `missing`, `cache_missing`, `error`; `upstream` is null until the branch is on origin |
| `archive` | `workspace.archive_outcome` | `action`: `removed`, `planned`, `skipped`, `failed`; a last row with an empty `repo` is the workspace directory (`skipped` when other files stay in it) |
| `run` | `workspace.run_outcome` | `action`: `ran`, `failed`, `skipped`; `returncode` is null for a timeout or a repo not run, negative for a signal |

`status --fetch` fetches each repo first (status is otherwise offline); a
repo whose fetch fails keeps its local state, with `detail` "fetch failed:
..." and an error. A repo whose git state cannot be read is an `error` row
with an `error` field. `status --all` covers every active workspace.

`create` and `add` in table format end with the workspace path as the last
stdout line; `-q` prints only the path. Other formats print records only.

## Piping

Pipe `github.repo`, `github.repo_hit` or `github.sweep_repo` records to
`create` or `add` with `--stdin`. Each record's full name (`full_name`, else
`repo`) is resolved through the inventory, so `workspace.protocol` and the
default branch apply; its clone URL is used when the inventory lacks it:

```bash
untaped github repos list --team acme/platform --format pipe | untaped workspace create NAME --stdin
```

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
| 1 | A `failed` row from `create`, `add` or `run` (see [run.md](run.md#failures)); archive refused (blocked repos); a `status --fetch` failure or `error` row; an unknown workspace name; `create` on a name whose directory already holds files |
| 2 | Usage error, including no NAME outside a workspace, an unknown repo, or `archive --force` without a terminal and without `--yes` |
| 3 | `status --check` and some repo would block archive |
| 4, 5 | By failure category: fix the setup (4), try again later (5) |
