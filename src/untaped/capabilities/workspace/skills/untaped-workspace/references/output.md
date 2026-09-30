# Output, pipes and exit codes

## Reading results

- Read `--format json` (or `yaml`) rather than tables; tables show a subset
  of each row's fields, and `--columns ?` lists them all.
- `--format raw --columns repo` prints bare values for shell loops.
- Rows about a repo carry an absolute `target_path`; on `unmatched` and
  `unavailable` rows it is the workspace directory.
- A `failed` row keeps its `detail` and adds an `error` object (`category`,
  `system`, `retryable`, `message`, `hint`) that tables leave out.
- `--quiet` (`-q`) mutes progress and success lines; data and errors still
  print.

## Pipes

`--format pipe` emits one record per line, tagged with its `kind`. A command
with `--stdin` reads plain lines or the record kinds it accepts; any other
kind exits 2.

| Consumer | Reads |
|---|---|
| `repos add --stdin` | URLs, `workspace.repo`, or GitHub repo records (`github.repo`, `github.repo_hit`, `github.sweep_repo`) |
| `repos remove --stdin` | repo names, `workspace.repo`, `workspace.sync_outcome` |
| `foreach --stdin` | repo names, `workspace.repo`, `workspace.status`, `workspace.sync_outcome` |
| `path --stdin` | workspace names, `workspace.workspace` |

Illustrations with invented names:

```bash
untaped github repos list --team acme/platform --format pipe \
  | untaped workspace repos add acme-platform --stdin --sync
untaped workspace list --format pipe | untaped workspace path --stdin
```

`repos list` on an empty workspace emits a single `workspace.repo.summary`
row with no `target_path`.

## Exit codes

| Code | Meaning | Next step |
|---|---|---|
| 0 | success; `skipped` rows alone are success | none |
| 1 | something failed: unknown workspace or repo, invalid `untaped.yml`, a failed git step, or a declined prompt | read the `failed` rows |
| 2 | usage error, or a prompt with no terminal and no `--yes` | fix the command line |
| 3 | `status --check` found a dirty or behind repo | act on the matching rows |
| 4 | fix the environment: settings, git or `$EDITOR` missing | fix the setup, not the command |
| 5 | temporary: git timed out or lost the network | retry later |

A run with several failures exits with the most severe one. With
`--format json`, stderr is JSON Lines naming each failure's `category`,
`system` and `hint`.
