# Workspace output

Every row is a record with an absolute `target_path`: the workspace
directory for `workspace.workspace`, the repo directory for the others.
`--columns ?` lists the fields of a table.

## Record kinds

| Command | Kind | Key fields |
|---|---|---|
| `list` | `workspace.workspace` | `name`, `repos`, `created_at`, `archived_at` |
| `create`, `add` | `workspace.repo_outcome` | `repo`, `action` (`created`, `checked_out`, `unchanged`, `failed`), `branch`, `base`, `read_only`, `detail` |
| `status` | `workspace.status` | `repo`, `branch`, `state` (`ok`, `missing`, `cache_missing`), `ahead`, `behind`, `modified`, `untracked`, `stashed`, `unpushed`, `blockers`, `detail` |
| `archive` | `workspace.archive_outcome` | `repo`, `action` (`removed`, `planned`, `skipped`, `failed`), `detail` |

`status --fetch` fetches each repo first (status is otherwise offline); a
repo whose fetch fails keeps its local state, with `detail` "fetch failed:
..." and an error. `status --all` covers every active workspace.

`create` and `add` in table format end with the workspace path as the last
stdout line; `-q` prints only the path. Other formats print records only.

## Piping

Pipe records to `create` or `add` with `--stdin` (`github.repo`,
`github.repo_hit` and `github.sweep_repo` rows; the clone URL field is used):

```bash
untaped github repos list --team acme/platform --format pipe | untaped workspace create NAME --stdin
```

`untaped recipe apply --stdin` reads `target_path` from `status` or
`create` rows:

```bash
untaped workspace status NAME --format pipe | untaped recipe apply acme/editorconfig --stdin --dry-run
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A `failed` row from `create` or `add`; archive refused (blocked repos); a `status --fetch` failure; an unknown workspace name |
| 2 | Usage error, including no NAME outside a workspace, an unknown repo, or `archive --force` without a terminal and without `--yes` |
| 3 | `status --check` and some repo would block archive |
| 4, 5 | By failure category: fix the setup (4), try again later (5) |
