---
name: untaped-workspace
description: Creates, inspects and archives task workspaces through the `untaped workspace` command (one directory per task holding git worktrees of several repos on a shared branch, safe archiving once work is pushed). Use when the user starts work on a ticket across repos, asks where a workspace is, or wants to clean one up.
---

# untaped workspace

A workspace is one directory per task, under `workspace.workspaces_dir`. Each
repo in it is a git worktree of a shared bare cache, on the task branch, or
read-only at a base branch. Create one per ticket, work and push in it, then
archive it.

## Setup

- Repos named `OWNER/NAME` or `NAME` are looked up in the GitHub inventory:
  it needs the GitHub token and the `github.inventory` orgs or teams. Full
  git URLs skip it.
- Git never prompts for credentials. Use a credential helper, or an SSH agent
  with `workspace.protocol` set to `ssh`.
- Directories, branch naming and parallelism are `workspace.*` settings
  (`untaped config list`).

## Commands

| When | Command |
|---|---|
| Start a task | `untaped workspace create NAME --repo OWNER/NAME --repo OWNER/NAME` |
| Add reference code you won't change | `untaped workspace create NAME --repo OWNER/NAME --read-only OWNER/NAME` |
| Bring in another repo later | `untaped workspace add NAME --repo OWNER/NAME` |
| Add repos from a GitHub listing | `untaped github repos list --team ORG/SLUG --format pipe` piped to `untaped workspace add NAME --stdin` |
| Find the directory | `untaped workspace path NAME` |
| See branches, uncommitted and unpushed work | `untaped workspace status NAME` |
| Check nothing blocks archiving (exit 3 while anything does) | `untaped workspace status NAME --check` |
| Run one command or script in each repo | `untaped workspace run NAME 'CMD'` |
| Clean up after pushing | `untaped workspace archive NAME` |
| List workspaces | `untaped workspace list`, `untaped workspace list --archived` |

In a terminal, `untaped workspace create` or `add` with no repos opens a
[picker](references/lifecycle.md#the-picker); agents pass `--repo` or `--stdin`.
`untaped workspace create NAME --empty` makes an empty workspace.

When the current directory is under `workspace.workspaces_dir`, you are in a
workspace: work in the checkouts already there, never clone the repos again
elsewhere, and omit NAME (it defaults to the current workspace, except on
`create`). `untaped workspace path` with no NAME prints the workspace
directory, and exits 2 outside one.

## Workflows

1. `untaped workspace create NAME --repo OWNER/NAME ...` creates one row per repo:
   `created` (a new branch from the base) or `checked_out` (an existing
   branch, resumed). A `failed` row names the cause; fix it and run
   `untaped workspace add NAME --repo ...` for that repo only.
2. `cd "$(untaped workspace path NAME)"` and work. Commit and `git push` in
   each repo; the upstream is already set.
3. `untaped workspace status NAME --check` exits 0 when nothing blocks
   archiving. It is the gate: `archive --dry-run` only previews and exits 0
   even when repos would block.
4. `untaped workspace archive NAME` removes the worktrees and keeps a record.
   Branches stay in the cache and on the remote, so creating a workspace with the
   same branch later resumes the work.

## Safety

- `--force` is destructive. Uncommitted work and commits made in a read-only
  (detached) repo cannot be recovered afterwards. Never run it without:
  1. previewing with `untaped workspace status NAME --check` or
     `untaped workspace archive NAME --dry-run`;
  2. showing the user which repos and what work would be lost;
  3. waiting for explicit approval;
  4. then `untaped workspace archive NAME --force --yes`.
- `run` has no preview. List the selection with `untaped workspace status NAME`
  and get approval before commands that rewrite history or push.
- Exit codes: 0 success, 1 a `failed` row or a refused archive, 2 usage
  error, 3 `status --check` found a blocker, 4 fix the environment, 5 retry
  later, 130 interrupted; details in
  [references/output.md](references/output.md#exit-codes).

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- `run`: quote the command; `-` reads a script from stdin (heredoc).
  Read-only repos are skipped unless `--include-read-only`.
- An unknown repo name exits 2 and suggests close matches; an ambiguous one
  exits 2 and lists the candidates.
- A branch can be checked out in one workspace at a time; a second workspace
  on the same branch gets a `conflict` row.
- `archive` refuses while any repo has uncommitted changes, stashes made on
  its branch, unpushed commits (read-only repos too), or initialised
  submodules; the hint says what to do for each.
- Stashes are shared by every workspace of a repo: `git stash list` shows
  other workspaces' stashes too. Never drop or clear a stash you did not make.

## References

| File | Read it when |
|---|---|
| [references/lifecycle.md](references/lifecycle.md) | creating or extending a workspace, the interactive picker, choosing branches and bases, read-only repos, how existing branches are reused, archiving and what blocks it |
| [references/run.md](references/run.md) | running a command or script in each repo, the `UNTAPED_*` variables, selecting repos, failures and timeouts |
| [references/output.md](references/output.md) | reading rows, piping records, or exit codes |
