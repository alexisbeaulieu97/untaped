---
name: untaped-workspace
description: Creates, inspects and archives task workspaces through the `untaped workspace` command (one directory per task holding git worktrees of several repos on a shared branch, safe archiving once work is pushed). Use when the user starts work on a ticket across repos, asks where a workspace is, or wants to clean one up.
---

# untaped workspace

A workspace is one directory per task, under `workspace.workspaces_dir`. Each
repo in it is a git worktree of a shared bare cache, on the task branch, or
read-only at a base branch. Create one per ticket, work and push in it, then
archive it.

| File | Read it when |
|---|---|
| [references/lifecycle.md](references/lifecycle.md) | creating or extending a workspace, choosing branches and bases, read-only repos, how existing branches are reused, archiving and what blocks it |
| [references/run.md](references/run.md) | running a command or script in each repo, the `UNTAPED_*` variables, selecting repos, failures and timeouts |
| [references/output.md](references/output.md) | reading rows, piping records, or exit codes |

## Commands

| When | Command |
|---|---|
| Start a task | `untaped workspace create NAME --repo OWNER/NAME --repo OWNER/NAME` |
| Add reference code you won't change | `untaped workspace create NAME --repo OWNER/NAME --read-only OWNER/NAME` |
| Bring in another repo later | `untaped workspace add NAME --repo OWNER/NAME` |
| Add repos from a GitHub listing | `untaped github repos list --team ORG/SLUG --format pipe` piped to `untaped workspace add NAME --stdin` |
| Find the directory | `untaped workspace path NAME` |
| See branches, uncommitted and unpushed work | `untaped workspace status NAME` |
| Done? (exit 3 while anything would block archive) | `untaped workspace status NAME --check` |
| Run one command or script in each repo | `untaped workspace run NAME 'CMD'` |
| Clean up after pushing | `untaped workspace archive NAME` |
| List workspaces | `untaped workspace list`, `untaped workspace list --archived` |

In a terminal, `untaped workspace create` with no repos opens a picker; agents pass `--repo` or `--stdin`.

`NAME` is optional on every command that takes a `NAME`, except `create`:
inside a workspace directory it is the current workspace. Agents should still
pass it.

## Workflow

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

## Pitfalls

- Quote the command; `-` reads a script from stdin (heredoc). Read-only repos
  are skipped unless `--include-read-only`.
- Repos are named `OWNER/NAME`, a unique bare `NAME`, or a full git URL. An
  unknown name exits 2 and suggests close matches from the inventory; an
  ambiguous one exits 2 and lists the candidates.
- Git never prompts for credentials. Use an SSH agent or a credential helper.
- A branch can be checked out in one workspace at a time; a second workspace
  on the same branch gets a `conflict` row.
- `archive` refuses while any repo has uncommitted changes, stashes made on
  its branch, unpushed commits (read-only repos too), or initialised
  submodules; the hint says what to do for each. `--force`, after a
  confirmation (`--yes` without a terminal), discards uncommitted work and
  removes the directories. Branch commits and stashes stay in the repo cache;
  commits made on a read-only (detached) repo do not.
- Stashes are shared by every workspace of a repo: `git stash list` shows
  other workspaces' stashes too. Never drop or clear a stash you did not make.
