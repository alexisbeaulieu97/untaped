# Workspace lifecycle

Create, extend and archive a task workspace. Options are in
`untaped workspace <command> --help`.

## Names

- A workspace name is one path segment: letters, digits, `.`, `_` and `-`,
  not starting with `.`. Anything else exits 2. The workspace lives at
  `workspace.workspaces_dir/NAME`.
- A name is never a path. Inside a workspace directory, commands other than
  `create` may omit it. Outside one, omitting it exits 2.
- Each repo directory is the repo name. When two repos in one workspace
  share a name, the later ones use `OWNER-NAME`.

## Naming repos

`--repo` and `--read-only` take, and `--stdin` reads:

- `OWNER/NAME`, or a bare `NAME` that is unique, both looked up in the GitHub
  inventory (`github.inventory` orgs and teams);
- a git URL or path: it contains `://`, starts with `/` or `~`, is
  `user@host:path`, or ends with `.git`. These skip the inventory.

An unknown or ambiguous name exits 2 and lists candidates. If a repo you
expect is not found, check the `github.inventory` orgs and teams settings.
`workspace.protocol` (`https` or `ssh`, default `https`) picks which clone
URL the inventory supplies.

## Branches and bases

Writable repos share one branch: `--branch`, else `workspace.branch_template`
with `{name}` replaced by the workspace name (default `{name}`). Every repo,
read-only ones included, is based on `--base`, else the repo's default
branch. `--branch` applies to writable repos only. A base missing on origin
fails that repo as `not_found`.

How a writable repo is checked out, with the row's `action` and `detail`:

| Branch state in the cache | Result |
|---|---|
| Exists nowhere | `created`, `from origin/BASE` |
| On origin only | `checked_out`, `tracking origin/BRANCH` |
| Local only | `checked_out`, `resumed local branch` |
| Local and origin, local behind or equal | `checked_out`, fast-forwarded, `tracking origin/BRANCH` |
| Local and origin, local ahead | `checked_out`, `resumed; ahead of origin/BRANCH` |
| Local and origin, diverged | `checked_out`, `resumed; diverged from origin/BRANCH` |

The upstream is set to `origin/BRANCH`, so `git push` works. A branch already
checked out in another worktree is a `failed` row with category `conflict`.

## Read-only repos

`--read-only` checks a repo out detached at `origin/BASE`, for reference code.
The row is `checked_out` with `read-only at origin/BASE`. A commit made there
is on no branch, so archiving counts it as unpushed and refuses.

## Partial failure

Each repo is independent. Repos that succeed stay in the workspace and are
recorded even when another fails; the command exits 1. Fix the cause and run
`untaped workspace add NAME --repo ...` for the failed repos. Repos already
present report `unchanged`.

## Archiving

`untaped workspace archive NAME` checks each repo offline, so push (or fetch)
first. A repo blocks archiving with any of:

- uncommitted changes;
- stash entries made on that repo's branch;
- commits not pushed: commits on `HEAD` that no remote branch has, in
  read-only repos too;
- "submodules: archive cannot verify or remove them safely" (any initialised
  submodule);
- "repo cache missing; local work cannot be checked";
- "git state unreadable: ..." (status `state` `error`, for example after the
  cache was recreated).

While any repo blocks, archive exits 1, removes nothing, and its hint says
what to do for each kind of blocker. `untaped workspace status NAME --check`
is the gate: it reports the same blockers and exits 3 (1 when a repo's git
state is unreadable). `--dry-run` previews with `planned` and `skipped` rows
and always exits 0.

`--force` archives anyway after a confirmation; without a terminal it needs
`--yes` (else exit 2). It discards uncommitted work and removes the
directories, deleting a worktree git refuses to remove. Branch commits and
stashes stay in the repo cache; commits made on a read-only repo do not. If
removing a repo fails, the workspace stays active so archive can be retried.
Stashes are shared by every workspace of a repo: never drop one you did not
make.

## After archiving

Archiving removes the worktrees and the workspace directory, and records the
workspace under `untaped workspace list --archived`. Other files left in the
workspace directory stay, with a `skipped` workspace row; `create` refuses
that name until the directory is moved aside. The branches stay in the
repo cache and on the remote, so a later `untaped workspace create NAME --repo
OWNER/NAME --branch BRANCH` resumes the work.

## The cache is load-bearing

Worktrees point into the bare cache at `workspace.cache_dir`. Do not delete
it while workspaces are active. Every cache write runs under a per-repo lock, so concurrent runs wait
rather than corrupt it.
