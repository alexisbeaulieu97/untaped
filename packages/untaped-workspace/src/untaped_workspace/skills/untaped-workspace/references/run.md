# Running a command in each repo

`untaped workspace run [NAME] CMD` runs one command or script in every
writable repo of a workspace. `NAME` may be left out inside a workspace: a
lone positional is always the command.

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
- A command that starts with a hyphen goes after `--`:
  `untaped workspace run NAME -- -x`. A single word starting with `--`
  (such as `--formt`) is taken as a mistyped option and exits 2.

Each run starts in the repo directory with stdin from `/dev/null`.
Background processes the command starts are stopped when it exits; one that
still holds the command's output is stopped shortly after the command exits. The row keeps the
command's own exit status. Processes that start their own session or process
group (such as `setsid`) are not stopped on exit, timeout or cancel, and can
keep the command's output open for a few more seconds.

## Environment

| Variable | Value |
|---|---|
| `UNTAPED_WORKSPACE` | Workspace name. |
| `UNTAPED_REPO` | Repo display name. |
| `UNTAPED_BRANCH` | Task branch; empty for a read-only repo. |
| `UNTAPED_BASE` | Base branch. |
| `UNTAPED_READ_ONLY` | `1` for a read-only repo, else `0`. |

## Choosing repos

Only writable repos run, unless `--include-read-only`. `--repo/-r` (repeatable)
takes a display name or a directory name; naming a read-only repo without
`--include-read-only` exits 2 with a hint.

`--stdin` reads the repo from `workspace.status`, `workspace.repo_outcome` or
`workspace.run_outcome` records (`repo`, else `dir`), or plain lines:

```bash
untaped workspace status NAME --format pipe | untaped workspace run NAME 'git stash' --stdin
```

- Read-only repos on stdin are dropped unless `--include-read-only`, so the
  `status` rows of a whole workspace pipe straight in.
- Records whose `workspace` is another workspace are ignored.
- With `--repo` as well, both sets run.

When nothing is left to run (an empty pipe, or only read-only repos), `run`
warns on stderr and exits 0.

## Before you run

`run` has no preview; follow [Pitfalls](../SKILL.md#pitfalls) before a
command that rewrites history or pushes.

## Failures

Every selected repo runs, and the exit code is 1 if any failed: a non-zero
status, a timeout, a signal (detail `killed by SIGTERM`) or a missing
directory (`missing`). `--fail-fast` stops starting new repos after the first
failure; the rest become `skipped` rows. Ctrl-C stops the running commands.

## Timeouts, parallelism and output

`--timeout` (seconds per repo) kills the command's whole process tree.
`--parallel/-j` sets how many repos run at once.

In the default table format, in a terminal or not, each finished repo prints
one block: a header (with the reason for a failure), the command's stdout,
then its stderr. Skipped repos print no block. A summary follows on stderr.
`--format json`, `yaml` and `pipe` emit `workspace.run_outcome` records only;
see [output.md](output.md).
