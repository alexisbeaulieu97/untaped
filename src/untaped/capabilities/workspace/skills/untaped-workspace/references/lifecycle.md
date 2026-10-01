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
with `{name}` replaced by the workspace name (default `{name}`). Each is
based on `--base`, else the repo's default branch. A base missing on origin
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
The row is `checked_out` with `read-only at origin/BASE`. Archiving ignores
unpushed commits there; only local changes count.

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
- commits not pushed (read-only repos: only local changes count);
- "repo cache missing; local work cannot be checked".

While any repo blocks, archive exits 1 and removes nothing.
`untaped workspace status NAME --check` reports the same blockers and exits 3.
`--dry-run` previews with `planned` rows. `--force` archives anyway and
discards that work after a confirmation; without a terminal it needs `--yes`
(else exit 2). If removing a repo fails, the workspace stays active so
archive can be retried.

## After archiving

Archiving removes the worktrees and the workspace directory, and records the
workspace under `untaped workspace list --archived`. The branches stay in the
repo cache and on the remote, so a later `untaped workspace create NAME --repo
OWNER/NAME --branch BRANCH` resumes the work.

## The cache is load-bearing

Worktrees point into the bare cache at `workspace.cache_dir`. Do not delete
it while workspaces are active. Every cache write runs under a per-repo lock, so concurrent runs wait
rather than corrupt it.
