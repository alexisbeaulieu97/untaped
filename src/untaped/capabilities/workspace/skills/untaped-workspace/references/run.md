# Running a command in each repo

`untaped workspace run [NAME] CMD` runs one command or script in every
writable repo of a workspace. `NAME` may be left out inside a workspace.

## Forms

```bash
untaped workspace run NAME 'git push -u origin HEAD'
untaped workspace run NAME ./bump.sh
untaped workspace run NAME - <<'EOF'
set -e
git fetch origin
git rebase origin/main
EOF
```

- A command string runs as `sh -c -- CMD`, so pipes and `&&` work. Quote it.
- A file is resolved against your current directory. It runs directly when it
  is executable and starts with `#!`; otherwise it runs through `sh`.
- `-` reads the script from stdin. Nothing piped, or `-` together with
  `--stdin`, exits 2.

Each run starts in the repo directory with stdin from `/dev/null`.

## Environment

| Variable | Value |
|---|---|
| `UNTAPED_WORKSPACE` | workspace name |
| `UNTAPED_REPO` | repo display name |
| `UNTAPED_BRANCH` | task branch; empty for a read-only repo |
| `UNTAPED_BASE` | base branch |
| `UNTAPED_READ_ONLY` | `1` for a read-only repo, else `0` |

## Choosing repos

Only writable repos run, unless `--include-read-only`. `--repo/-r` (repeatable)
takes a display name or a directory name; naming a read-only repo without
`--include-read-only` exits 2 with a hint.

`--stdin` reads the repo from `workspace.status`, `workspace.repo_outcome` or
`workspace.run_outcome` records (`repo`, else `dir`), or plain lines:

```bash
untaped workspace status NAME --format pipe | untaped workspace run NAME 'git stash' --stdin
```

## Failures

Every selected repo runs, and the exit code is 1 if any failed (a non-zero
status, a timeout, or a missing directory, reported as "missing").
`--fail-fast` stops starting new repos after the first failure; the rest
become `skipped` rows. Ctrl-C stops the running commands.

## Timeouts, parallelism and output

`--timeout` (seconds, above 0, default 600) kills the command's whole process
tree. `--parallel/-j` sets concurrent repos, defaulting to `workspace.parallel`.

In a terminal, each finished repo prints one block, with the reason in the
header for failures, then a summary on stderr. `--format json`, `yaml` and
`pipe` emit `workspace.run_outcome` records only; see
[output.md](output.md).
