# Workspace lifecycle

Create, extend, archive and remove a task workspace. Options are in
`untaped workspace <command> --help`.

Contents: names, naming repos, the picker, branches and bases, read-only
repos, partial failure, the history backfill, archiving and force, after
archiving, removing, the repo store.

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

- `OWNER/NAME`, or a bare `NAME` that is unique, both looked up in the repos
  every installed repo provider lists (GitHub's are its `github.inventory`
  orgs and teams, else `github.default_org`; listing them needs the token).
  Lists are cached for 6 hours;
- a clone URL: `https://…`, `ssh://…` or `user@host:path`. It skips the
  lookup and its repo is named by its path (`acme/api`). Local paths and
  `file://` URLs are refused. A URL on a host a plugin claims (GitHub's)
  must name a repo that plugin's settings can reach.

An unknown or ambiguous name exits 2 and lists candidates; a name no provider
could look up says which setting to set. The provider decides each repo's
clone URL (`github.git_protocol`: `https` or `ssh`). The URL is saved with the
workspace: after changing that setting, `untaped workspace repos resolve NAME`
asks each repo's provider again and saves the new URL (a URL naming another
repo is refused, `failed`).

## The picker

In a terminal, `create` and `add` with no `--repo`, `--read-only` or
`--stdin` open an interactive picker. Without a terminal, `add` exits 2 and names
those flags, `create` with no NAME exits 2, and `create NAME` makes an empty
workspace. `create NAME --empty` makes an empty workspace without the picker,
in a terminal or not, and exits 2 with `--repo`, `--read-only`, `--stdin`,
`--branch` or `--base`. Agents never rely on the
picker.

- `create` with no NAME asks for one first, refusing invalid names, active
  workspace names and non-empty existing directories.
- It lists every repo provider's repos (opened from the cache, then
  refreshed; the footer says how fresh each provider's are), repos workspace
  has in the repo store (as `host/[owner/]name`,
  checked out from their stored URL) and any git URL typed in. `add` leaves out repos already
  in the workspace.
- Each selected repo has a mode (write or read-only), a base (completes from
  the stored branches) and a branch (empty uses `workspace.branch_template`).
  `--branch` and `--base` without repo flags prefill these.
- Keys: `space` toggles a repo (`enter` too, in the list), `/` searches,
  `tab` switches pane, `enter` edits a setting in the selected pane and
  `←`/`→` change it, `ctrl-s` creates or adds the selection (`create` also
  with nothing selected: an empty workspace), `ctrl-r` refreshes the
  providers' lists, `esc` clears a typed search and otherwise quits like `ctrl-c`,
  which asks first when anything is selected and creates nothing.

## Branches and bases

Writable repos share one branch: `--branch`, else `workspace.branch_template`
with `{name}` replaced by the workspace name (default `{name}`). Every repo,
read-only ones included, is based on `--base`, else the repo's default
branch. `--branch` applies to writable repos only. A base missing on origin
fails that repo as `not_found`.

How a writable repo is checked out, with the row's `action` and `detail`:

| Branch state in the repo store | Result |
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

## The history backfill

The repo store keeps repos blobless: a checkout fetches the files of its own
commit only. After the worktree is in place, `create` and `add` fetch the
files of every commit of the remote's branches and tags, so `git blame`,
`git log -p` and checking out an old tag work offline. The backfill never
fails a repo: when it stops (a timeout, a dropped connection), the row is
still `created` or `checked_out`, the command exits 0, and a warning says
"history backfill of REPO stopped: ...; `untaped workspace status --fetch
NAME` resumes it". Until then, git fetches missing files on demand. Each
`status --fetch` backfills what the fetch brought; one that stops again says
so in the row's `detail`.

## Archiving

`untaped workspace archive NAME` checks each repo offline, so push (or fetch)
first. A repo blocks archiving with any of:

- uncommitted changes;
- stash entries made on that repo's branch;
- commits not pushed: commits on `HEAD` that no remote branch has, in
  read-only repos too;
- "submodules: archive cannot verify or remove them safely" (any initialised
  submodule);
- "repo missing from the repo store; local work cannot be checked";
- "git state unreadable: ..." (status `state` `error`, for example after the
  store repo was recreated).

While any repo blocks, archive exits 1, removes nothing, and its hint says
what to do for each kind of blocker. `untaped workspace status NAME --check`
is the gate: it reports the same blockers and exits 3 (1 when a repo's git
state is unreadable). `--dry-run` previews with `planned` and `skipped` rows
and always exits 0.

`--force` archives anyway after a confirmation; without a terminal it needs
`--yes` (else exit 2). It discards uncommitted work and removes the
directories, deleting a worktree git refuses to remove. Branch commits and
stashes stay in the repo store. Never run it without the four steps in
[Safety](../SKILL.md#safety).

If removing a repo fails, the workspace stays active so archive can be retried.
Without `--force`, each repo is checked again just before it is removed:
work made after the first check fails that repo (a `conflict`), and it stays.
For stashes, which every workspace of a repo shares, see
[Pitfalls](../SKILL.md#pitfalls).

## After archiving

Archiving removes the worktrees and the workspace directory, and records the
workspace under `untaped workspace list --archived`. Other files left in the
workspace directory stay, with a `skipped` workspace row; `create` refuses
that name until the directory is moved aside. The branches stay in the
repo store and on the remote, so a later `untaped workspace create NAME --repo
OWNER/NAME --branch BRANCH` resumes the work.

## Removing

`untaped workspace remove NAME` gives the space back. It takes an active
workspace (archived first, with archive's checks, refusals and `--force`) or
an archived one, and drops every record of NAME. Then, for each of its repos
that no other workspace, active or archived, names, it releases the repo
from the repo store: workspace's refs (`origin/*`, the tags), its file and
the local branches that no worktree has checked out and whose commits are
all on the remote go, and the repo itself when no other plugin uses it.

- Before anything changes, a branch of the workspace with commits the remote
  lacks refuses the removal (exit 1, "branch BRANCH: N commits not pushed"),
  as archive's blockers do. Push it from a workspace on that branch, or pass
  `--force`, which deletes it.
- Other branches with unpushed commits or a stash made on them stay, and
  keep the repo (listed under `held by branches` in `untaped git store`).
  `--force` deletes them too, and the preview and the confirmation name
  them. A stash itself is never deleted, even when its branch is.
- `remove` always confirms; without a terminal it needs `--yes` (else exit
  2). `--dry-run` previews with `planned` and `skipped` rows and exits 0.

Each repo's row says what happened: `removed` (with the space freed),
`released` (`kept: ...` names who still uses the repo: another plugin, a
branch, a stash, a worktree added by hand), `kept` (another workspace uses
it, or a workspace worktree was added to it meanwhile), `skipped` (not in the repo store) or `failed`. A last row with an empty
`repo` is the workspace itself.

## The repo store is load-bearing

Worktrees point into the repo's copy in the git plugin's repo store
(`git.store_dir`; `untaped git store` reports it). Never delete a store repo
by hand while workspaces use it: `remove` releases what workspace no longer
needs. The store locks each repo for every write, so concurrent runs wait
rather than corrupt it. `create`, `add`, `archive` and `remove` of one
workspace also wait for each other (`archive` and `remove` hold the
workspace from their check, through any confirmation, to the removal): an
`add` queued behind an `archive` then fails as not found.
