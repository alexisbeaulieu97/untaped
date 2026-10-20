---
description: "Read before naming repos, choosing branches or bases, archiving or removing: how a workspace's repos, branches, archive and removal rules work."
---

A workspace is one directory per task, `workspace.workspaces_dir/NAME`. Each
repo in it is a git worktree of that repo's copy in the git plugin's repo
store (`git.store_dir`), on the task branch, or read-only at a base branch or
tag. Its life: `create`, work and push, `archive` (the worktrees go, the
branches stay), then `remove` (the records go and the repo store releases
the repos).

## Names

- A workspace name is one path segment: letters, digits, `.`, `_` and `-`,
  not starting with `.`. Anything else exits 2. A name is never a path.
- Inside a workspace directory, every command that takes NAME, except
  `create` and `remove`, takes the workspace from the current directory when
  NAME is left out; outside one, leaving it out exits 2.
- Each repo directory is the repo name. When two repos in one workspace
  share a name, the later ones use `OWNER-NAME`.

## Naming repos

`--repo` and `--read-only` take, and `--stdin` reads:

- `OWNER/NAME`, or a bare `NAME` that is unique, both looked up in the repos
  every installed repo source lists (the plugins under "Where the repos you
  can name in workspace commands come from" on `untaped agents workspace`).
  The lists are cached (`workspace.caches.repo_source_repos.max_age_seconds`).
  When two sources list the same name,
  `untaped plugin rank workspace.repo_source repos PLUGIN...` sets their order.
- A clone URL: `https://…`, `ssh://…` or `user@host:path`. It skips the
  lookup, and its repo is named by its path (`acme/api`). Local paths and
  `file://` URLs are refused. A URL on a host a plugin claims must name a
  repo that plugin's settings can reach.

The source decides each repo's clone URL, and the URL is saved with the
workspace; `untaped workspace repos resolve NAME` asks again after a source's
setting changes.

Git never prompts for credentials when untaped runs it. Fetching from a host
a plugin claims uses that plugin's token. Over https, `git push` in a worktree
asks `untaped git credential` after your own credential helpers (instead of
them with `git.untaped_helper_only`). Any other host needs a credential
helper or an SSH agent.

## Branches and bases

Writable repos share one branch, and every repo, read-only ones included,
has one base. A base names a branch or a tag on origin; a branch wins when
both exist, and a base that is neither fails that repo as `not_found`.

How a writable repo is checked out, with the row's `action` and `detail`:

| The branch in the repo store | Result |
|---|---|
| Exists nowhere | `created`, `from origin/BASE` (`from tag BASE` for a tag) |
| On origin only | `checked_out`, `tracking origin/BRANCH` |
| Local only | `checked_out`, `resumed local branch` |
| Local and origin, local behind or equal | `checked_out`, fast-forwarded, `tracking origin/BRANCH` |
| Local and origin, local ahead | `checked_out`, `resumed; ahead of origin/BRANCH` |
| Local and origin, diverged | `checked_out`, `resumed; diverged from origin/BRANCH` |

The upstream is set to `origin/BRANCH`, so `git push` works. A branch is
checked out in one worktree at a time: a branch already checked out in
another workspace is a `failed` row with category `conflict`.

`--read-only` checks a repo out detached at `origin/BASE`
(`read-only at origin/BASE`, or `read-only at tag BASE`). A commit made there is on no
branch, so archiving counts it as unpushed and refuses.

## The history backfill

The repo store keeps repos blobless. After the worktree is in place, `create`
and `add` fetch the files of every commit of the remote's branches and tags,
so `git blame`, `git log -p` and old tags work offline. The backfill never
fails a repo: when it stops, the row is still `created` or `checked_out`, the
command exits 0, a warning names the repo, and
`untaped workspace status --fetch NAME` resumes it. Until then git fetches missing files on demand.

## Archiving

`archive` checks each repo offline: it sees origin as of the last push or
fetch. A repo blocks archiving with any of:

- uncommitted changes;
- stash entries made on that repo's branch;
- commits on `HEAD` that no remote branch nor the base tag has, in read-only
  repos too;
- an initialised submodule;
- the repo missing from the repo store;
- unreadable git state (`status` shows `state` `error`).

While any repo blocks, archive exits 1, removes nothing, and its hint says
what to do for each blocker. `untaped workspace status NAME --check` reports
the same blockers.

`--force` archives a blocked workspace after a confirmation: it discards
uncommitted work and commits made in a read-only repo, which cannot be
recovered, and removes the directories. Branch commits and stashes stay in the repo store. Without
`--force`, each repo is checked again just before it is removed, and work
made after the first check fails that repo as a `conflict`. If removing a
repo fails, the workspace stays active so archive can run again.

Archiving removes the worktrees and the workspace directory and records the
workspace under `untaped workspace list --archived`. Other files left in the
directory stay, with a `skipped` workspace row, and `create` refuses that
name until the directory is moved aside. The branches stay in the repo store
and on the remote, so
`untaped workspace create NAME --repo OWNER/NAME --branch BRANCH` later
resumes the work.

## Removing

`remove` takes an active workspace (archived first, with archive's checks
and `--force`) or an archived one, and drops every record of NAME. For each
of its repos that no other workspace, active or archived, names, it releases
the repo from the repo store: workspace's refs, and the local branches that
no worktree has checked out and whose commits are all on the remote, go; the
repo itself goes when no other plugin uses it.

- The workspace's own branch with commits no remote branch has refuses the
  removal (exit 1, "branch BRANCH: N commits not pushed"); `--force` deletes
  it, and also a worktree git refuses to remove.
- Other branches with unpushed commits or a stash made on them stay and keep
  the repo (`held by branches` in `untaped git store`); `--force` deletes
  them too, and the preview and the confirmation name them. A stash itself
  is never deleted, even when its branch is.

## Shared by every workspace of a repo

- Stashes: `git stash list` in one workspace shows the stashes made in the
  others too.
- The repo store: worktrees point into it, so deleting a store repo by hand
  breaks every workspace using it; `remove` releases what workspace no longer
  needs. The store locks each repo for every write, so concurrent runs wait.
  `create`, `add`, `archive` and `remove` of one workspace also wait for each
  other (`archive` and `remove` hold it from their check to the removal): an
  `add` queued behind an `archive` then fails as not found.
