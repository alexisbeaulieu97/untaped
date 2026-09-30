# Workspaces

A *workspace* is a directory that holds a collection of git repos
managed together — typically one per environment, project, or team.
`untaped workspace` lets you declare what's in a workspace, clone or
update everything in one shot, run a command across every repo, and
jump between workspaces from your shell.

The two homes of workspace state:

- **Per-workspace manifest** — `<workspace-dir>/untaped.yml` declares
  the workspace's name, its default branch, and its repos. This is the
  source of truth for what belongs in a workspace.
- **Central registry** — a `workspace.workspaces` list in
  `~/.untaped/state.yml` mapping `name → path`. Just enough state to
  power `list`, `path <name>`, and workspace-name lookups.

Manifests are checked into a shared directory or a git repo if you
want; the registry is local-only.

## Quick tour

```bash
untaped workspace init prod                     # new workspace at ~/.untaped/workspaces/prod
untaped workspace repos add prod git@github.com:acme/api  # add a repo
untaped workspace repos list prod               # inspect the declared repos
untaped workspace branch set prod main          # update manifest default branch
untaped workspace sync prod                     # clone everything in the manifest
untaped workspace status prod                   # per-repo git status
```

To use another profile's settings (for example its `workspace.workspaces_dir`),
pass the root `--profile` option anywhere before a `--` separator (tokens
after `--` belong to the command):

```bash
untaped --profile work workspace init prod
untaped workspace sync prod --profile work
```

### Choosing the workspace

Every command that acts on one workspace takes it as its first
positional argument, `WS`:

- a registered name (`prod`);
- a path inside a workspace (`.`, `..`, `~/work/prod`, `./prod`, or
  anything containing `/`); the path must exist, and untaped walks up
  from it to the nearest `untaped.yml`, so an unregistered workspace
  works too. Workspace names therefore cannot start with `~`;
- omitted: the workspace containing the current directory (the same
  walk-up as `.`).

`repos add` and `repos remove` take the repos after the workspace, so
they need `WS` whenever repos are given positionally
(`untaped workspace repos add . <url>`; a lone argument is the
workspace, so `repos add prod` exits `2` with `missing URL (or --stdin)`);
with `--stdin` it may be omitted. `foreach [WS] CMD` and `branch set [WS] BRANCH` read one
positional as the command or branch and two as the workspace followed by
it. `sync`, `status` and `foreach` take `--all` instead of `WS` to act
on every registered workspace.

## The manifest — `untaped.yml`

```yaml
# <workspace-dir>/untaped.yml
name: prod                    # registry name (optional; falls back to dirname)
defaults:
  branch: main                # branch used when a repo doesn't specify its own
repos:
  - url: git@github.com:acme/api.git
    name: api                 # local directory name (derived from URL if omitted)
    branch: develop           # per-repo override; otherwise inherits defaults.branch
  - url: git@github.com:acme/web.git
  - url: https://github.com/acme/docs.git
    name: docs
```

Branch resolution at clone time follows a cascade: per-repo `branch` >
`defaults.branch` > the remote's HEAD. `workspace branch apply` only
uses explicit manifest branch targets (`repos[].branch` or
`defaults.branch`) and skips repos with no target. It checks out an
existing local branch when present, or creates a local tracking branch
when `origin/<branch>` resolves to a commit. If the branch is missing
locally and no usable `origin/<branch>` exists, it skips the repo with
`branch not found locally or on origin` unless `--create` is passed, in
which case it creates a local branch from the current clean HEAD.
Subsequent `sync`s will not check out a different branch for you — if the
on-disk branch diverges from the manifest's target, `sync` skips that
repo with a warning, so a stale `defaults.branch` can't kidnap a repo
you've moved to a feature branch.

`repos[].name` is what shows up on disk under the workspace directory
and what you pass to `--repo` / `repos remove`. Names and URLs must both be
unique within a manifest; names are compared case-insensitively, since
`api` and `API` are the same directory on macOS and Windows. A repo name
(explicit, via `--repo-name`, or derived from the URL) must be a single
path segment: not empty, not `.` or `..`, no `/`, `\`, `:`, or NUL, and
not `untaped.yml`. The same rule applies to the name passed to
`workspace init`. A manifest that breaks it is rejected when loaded.

## Commands

### `list`

```bash
untaped workspace list                                      # tabular
untaped workspace list --profile work --format raw --columns name
```

Lists the central registry — every workspace `untaped workspace` knows about
by name and path.

### `repos list`

```bash
untaped workspace repos list [WS] [--format json|yaml|table|raw|pipe] [--columns ...]
```

List the repos declared in one workspace's manifest. Each repo produces
one `workspace.repo` row with the repo name (first, so `--format raw`
prints repo names), workspace name, manifest path, default branch, repo
count, repo URL, per-repo branch override, effective target branch, and
the clone's absolute `target_path`. The table shows `repo`, `url` and
`target_branch`; `--columns ?` lists every field. Empty manifests still
emit a single `workspace.repo.summary` row with `repo_count: 0` and no
`target_path`.

`repos list` reads `untaped.yml` only. It does not inspect git status or
remote state; use `untaped workspace status` for live checkout data.

### `init`

```bash
untaped workspace init <name> [--path <dir>] [--branch <default>]
                             [--format json|yaml|table|raw|pipe] [--columns ...]
```

Creates a new workspace named `<name>` and registers it. The default
location is `<workspaces_dir>/<name>` (the `workspaces_dir` profile setting
defaults to `~/.untaped/workspaces` and is profile-overridable).
Pass `-p / --path` to override the location for a one-off workspace
that lives elsewhere. Writes a starter `untaped.yml` in the directory.
`init` and `import` refuse a name that is already registered before
writing anything, and remove the manifest they just wrote if
registration fails. If the directory already has an `untaped.yml`, use
`untaped workspace adopt <dir>` to register it instead. `init` prints
`initialized workspace '<name>' at <dir>` on stderr (muted by `-q`) and
one `workspace.init_outcome` row (`name`, `action: created`,
`target_path`) on stdout.

### `adopt`

```bash
untaped workspace adopt <path> [--name <name>]
```

Adopt existing workspace state at a path.

If `<path>/untaped.yml` already exists, `adopt` validates that manifest
and registers the path without rewriting the file. The manifest's
`name` is used as the registry name by default; `--name` can register
the same on-disk workspace under a different registry name without
mutating `untaped.yml`.

If no manifest exists, `adopt` initialises a workspace from a directory
that already contains git clones. Each immediate subdirectory
containing `.git` is recorded in the new manifest with its current
`origin` URL and checked-out branch (a detached HEAD becomes `branch:
null`; clones missing an `origin` emit a stderr warning and are
skipped). The on-disk clones stay where they are — `adopt` does
**not** rewire them to share objects with the bare cache; the cascade
only seeds *new* clones via `git clone --reference --dissociate`.

```bash
git clone git@github.com:acme/api  ~/work/prod/api
git clone git@github.com:acme/web  ~/work/prod/web
untaped workspace adopt ~/work/prod --name prod

# Later, on another machine or after removing the registry entry:
untaped workspace adopt ~/work/prod
```

### `forget`

```bash
untaped workspace forget <name> [--prune] [--yes] [--dry-run]
                               [--format ...] [--columns ...]
```

Remove a workspace from the central registry. The on-disk manifest and
clones are preserved by default — `forget` is the inverse of `init` /
`adopt`, not of `sync --prune`. Pass `--prune` to also delete what
untaped manages in the workspace directory; the command previews the
workspace name and its absolute path and confirms the destructive
operation unless `--yes` / `-y` is passed. Without a terminal and
without `--yes` it exits `2` (`forget requires --yes when not
interactive`) and changes nothing; `--prune --dry-run` needs no
terminal. A declined prompt exits `1`
(`cancelled; no changes made`) without changing registry state or
files. A forgotten workspace produces one `workspace.forget_outcome` row
(`name`, `action: forgotten`, `pruned`, or `planned` under `--dry-run`,
`target_path`).

`--prune --dry-run` changes nothing (it wins over `--yes`): it runs the
same checks, lists on stderr every path the prune would delete (and
notes that the workspace directory goes too if nothing else is left),
and prints one `planned` row. An unsafe clone fails the dry run exactly
as it would fail the prune. `--dry-run` without `--prune` is a usage
error (exit `2`), as for `sync`.

`forget --prune` deletes only:

- declared repo clones and immediate child directories containing their
  own `.git` (undeclared/orphan clones), after they pass the safety check;
- symlinks standing in for a declared repo or pointing at a git clone
  (the link only, never its target);
- `untaped.yml`.

Everything else — loose files, non-git child directories, and declared
repo directories without their own `.git` — is left alone. The
workspace directory is removed only if it is empty afterwards;
otherwise the command prints a `warning: left <path> in place: …` line
naming what was kept.

Pruning is refused (mirroring `remove --prune`) when any clone that
would be deleted has unsafe local state or cannot be inspected: dirty,
untracked, or staged work, stash entries, or commits not reachable from
local remote-tracking refs, including commits reachable only from local
tags. With `--prune`, a missing manifest is refused (delete the
directory manually); a missing directory is tolerated. The registry
entry is removed regardless.

### `import`

```bash
untaped workspace import <source.yml> <dest> [--name <name>] [--sync]
```

Adopt an existing manifest into a new workspace directory. Useful when
a colleague shares a YAML file describing their workspace setup. Pass
`--sync` to clone the imported repos immediately (only the repos in
the imported manifest — same scope as `repos add --sync`).

### `repos add`

```bash
untaped workspace repos add WS <url>... [--branch <b>] [--repo-name <alias>]
                                        [--sync] [--format ...] [--columns ...]
untaped workspace repos add [WS] --stdin
```

Add one or more repo URLs to the workspace's manifest. Multiple URLs
may be passed positionally or via `--stdin`; `--branch` and
`--repo-name` apply uniformly to every URL in the batch (use one URL
per invocation for per-repo overrides). `--repo-name` with more than
one URL is a usage error (exit `2`). Each added repo produces one
`workspace.add_outcome` row (`workspace`, `repo`, `url`, `branch`,
`action: added`, `target_path`; the table shows `repo`, `url`, `branch`
and `action`). With `--sync`, also clone the URLs
that landed (a duplicate that fails to register won't try to clone);
the command then prints the `workspace.sync_outcome` rows instead of
the add rows.

`--stdin` reads one URL per line, or a `--format pipe` stream of
`github.repo`, `github.repo_hit` or `github.sweep_repo` records (their
`clone_url`, else `url`) or `workspace.repo` records (their `url`), so
`untaped github search repos --org acme --format pipe | untaped workspace
repos add prod --stdin` works. A pipe record of any other kind exits `2`.

### `repos remove`

```bash
untaped workspace repos remove WS <repo>... [--prune] [--yes] [--dry-run]
                                            [--format ...] [--columns ...]
untaped workspace repos remove [WS] --stdin
```

Remove one or more repos from the manifest, identified by URL or
alias. `--prune` also deletes the local clone after the SDK batch
preview and confirmation, unless `--yes` / `-y` is passed; without a
terminal and without `--yes` (or `--dry-run`) it exits `2` and changes
nothing. A declined
prompt exits `1` (`cancelled; no changes made`) without changing the
manifest or local clone. `--dry-run` prints one `planned` row per
identifier and changes nothing (it wins over `--yes`). Each removed
repo produces one `workspace.remove_outcome` row (`workspace`, `repo`,
`action`, `pruned`). The
prune is refused if the clone has unsafe local
state: dirty/untracked/staged work, stash entries, or commits not
reachable from local remote-tracking refs, including commits reachable
only from local tags. The safety check is offline and does not fetch,
inspect upstream config, or require an `origin` remote. Stale
remote-tracking refs are trusted as the offline safety boundary; fetch
the clone yourself first if you need the check to reflect current
remote state. With
`--stdin`, reads repo identifiers one per line, or the `repo` field of
a `workspace.repo` / `workspace.sync_outcome` pipe stream — works
nicely with `fzf`:

```bash
untaped workspace status prod --format raw --columns repo \
  | fzf -m \
  | untaped workspace repos remove prod --stdin
```

### `branch`

```bash
untaped workspace branch set [WS] <branch> [--repo <repo>] [--apply [--create]]
untaped workspace branch unset [WS] [--repo <repo>] [--format ...] [--columns ...]
untaped workspace branch apply [WS] [--repo <repo>]... [--create]
```

Set or unset branch metadata in `untaped.yml`. Without `--repo`, the
command updates `defaults.branch`; with `--repo`, it updates the
matching repo override by alias or URL. `branch unset` prints one
`workspace.branch_unset_outcome` row (`workspace`, `repo`, `branch`,
`action: updated`).

`branch set` and `branch unset` never run `git checkout` by default.
They only change the target branch used for future clones, `branch
apply`, and `sync` branch-mismatch checks.

Use `branch apply` to checkout existing local clones to the manifest's
target branch:

```bash
untaped workspace branch set prod main
untaped workspace branch apply prod

# or do both steps in one command
untaped workspace branch set prod main --apply
```

`branch apply` fetches first, refuses dirty or diverged repos, and emits
one row per repo (with the clone's `target_path`) whose `action` is
`checked_out`, `unchanged`, `skipped`, or `failed`
(a fetch, status, or checkout error, with a structured `error`; the command
then exits non-zero, as `sync` does). Missing
clones and repos without a target branch are skipped. If the target
branch resolves to a commit on `origin` but not locally, `branch apply`
creates a local tracking branch. If the target branch is missing locally
and no usable `origin` ref exists, the repo is skipped with `branch not
found locally or on origin`, so a typo such as `branch set mian` cannot
create a stray branch in every repo. Pass `--create` (to `branch apply`
or `branch set --apply`) to create it from the current clean HEAD
instead.

The `branch apply` table shows `repo`, `action` and `detail`, plus
`target_branch` when the repos target different branches.

### `sync`

```bash
untaped workspace sync [WS | --all]
                       [--repo <repo>]... [--prune [--yes | --dry-run]]
                       [--timeout <seconds>] [--parallel N]
```

Reconcile each repo on disk with the manifest:

| Action       | When                                                      |
| ------------ | --------------------------------------------------------- |
| `cloned`     | Repo is in the manifest but missing on disk.              |
| `pulled`     | Repo exists; on the manifest's target branch; behind.     |
| `unchanged`  | Repo exists; nothing to do.                               |
| `skipped`    | Deliberately left alone: dirty, diverged, on a different branch, not a git repository, or an unsafe orphan (with a reason). |
| `failed`     | A clone, fetch, status, or pull errored; `detail` names the step and git's error. |
| `removed`    | Local clone is not in the manifest, and `--prune` is set. |
| `planned`    | `--prune --dry-run`: a safe orphan clone `--prune` would remove. |
| `unmatched`  | `--all --repo <repo>` was passed and `<repo>` isn't in this workspace's manifest — `repo` carries the unmatched identifier. |
| `unavailable` | `--all` hit a registered workspace whose manifest could not be read — `repo` is empty and `detail` explains the manifest failure. |

Every row carries an absolute `target_path`: the repo's clone
directory, or the workspace directory for `unmatched` and `unavailable`
rows. Earlier releases spelled these actions `clone`, `pull`,
`up-to-date`, `skip` and `remove`. The table shows `repo`, `action` and
`detail` (led by `workspace` under `--all`); `--columns ?` lists every
field.

A `pulled` row fast-forwards the checked-out branch to its configured upstream
(`@{upstream}`), which need not be `origin/<same name>`. A branch with
no upstream is skipped with `no upstream` rather than reported as up to
date.

`sync` exits non-zero when any row is `failed` (after printing every row),
so scripts and CI notice a clone or fetch that did not happen; `skipped`
rows alone keep exit `0`. A `failed` row also carries `error`: its
`category`, the `system` responsible (`git`), whether a retry can help
(`retryable`), the `message` and a `hint` (json, yaml and pipe output;
tables leave it out). The exit code follows the most severe row: `5` when
a git call timed out or lost the network (retry later), `4` when git is
not installed, else `1`. `add --sync` and `import --sync` follow the same
rule. With `--format json|yaml|pipe` (or `UNTAPED_DIAGNOSTICS=json`) the
stderr messages are JSON Lines too; see [Exit codes](../reference/exit-codes.md).

`--repo <repo>` / `-r <repo>` limits sync to specific repos (repeatable);
`--all` runs sync against every workspace in the registry — handy as
a morning routine.

Under `--all`, missing, unreadable, YAML-invalid, or schema-invalid
workspace manifests are row-level `unavailable` outcomes, not command
failures. The row uses `repo=""` because no repo was selected or
inspected. Malformed registry entries still abort before the sweep; the
registry is the index and is not repaired automatically.

`--timeout <seconds>` caps every git invocation in this sync run, so a
hung remote can't strand a `--all` sweep. Defaults are 60s for
local-only git ops and 600s for clone/fetch; passing `--timeout 30` caps
both at 30s (CI-friendly fail-fast). A clone that fails or times out
removes the directory it created, so the next sync retries the clone
instead of treating a partial directory as an existing repo.

Git does not wait for interactive credential prompts: stdin is closed,
terminal/credential-manager prompts are disabled (`GIT_TERMINAL_PROMPT=0`,
`GCM_INTERACTIVE=never`), and ssh runs with `GIT_SSH_COMMAND="ssh -o
BatchMode=yes"`, so a remote that needs credentials fails that repo
instead of hanging the sweep. If you configure ssh yourself
(`GIT_SSH_COMMAND`, `GIT_SSH`, or the `core.sshCommand` git setting),
untaped leaves it alone; add `-o BatchMode=yes` to keep the fail-fast
behavior. Configure an SSH agent or a credential helper for private
remotes.

`--parallel N` / `-j N` runs up to `N` repo sync jobs concurrently.
This works for a single workspace and for `--all`; the cap is global
across every selected repo, not per workspace. Without `--parallel`,
sync uses the `workspace.parallel` profile setting, or
`min(8, 2 × CPUs)` when it is unset, so sync is parallel by default
(`-j 1` or `untaped config set workspace.parallel 1` makes it serial).
The value is clamped to `2 * os.cpu_count()` with a stderr warning when
needed. `-j` below `1` is a usage error (exit `2`); a `workspace.parallel`
below `1` is an invalid config value (exit `4`).

Sync output remains deterministic even when repo jobs finish out of
order: workspace input order first, then unmatched selector rows,
manifest-order sync rows, and prune rows last. `--prune` runs as a
serial second phase after all clone/fetch/pull jobs finish, so it
doesn't race in-flight clones. Progress and the final summary are
stderr-only (the summary reads `sync: 2 cloned, 1 failed`, or
`sync: nothing to do`); `json`, `yaml`, `raw`, and `pipe` stdout rows
keep the same `SyncOutcome` shape.

`sync --prune` deletes immediate child git clones that are no longer
declared in the manifest only when the clone is safe to delete. Unsafe
orphans are not deleted; they emit a `skipped` row whose detail begins with
`unsafe local state:`. Multiple blockers render as
`unsafe local state: <first>; +N more`. Uninspectable/corrupt orphans
remain distinct as `not a usable git repo`; symlinked git candidates are
also skipped instead of followed or deleted. Like `remove --prune` and
`forget --prune`, `sync --prune` previews the safe orphans it is about
to delete (workspace, repo, absolute path) and asks once for
confirmation; pass `--yes` / `-y` to skip the prompt. Without a TTY and
without `--yes` it prints the sync summary and rows and exits `2` with an
error instead of deleting; declining keeps every orphan, prints the rows,
and exits `1` with `cancelled; no changes made`. There is nothing to
confirm, and no `--yes` needed, when no safe orphans exist. Safety is
re-checked right before each delete. The same local remote-tracking ref
boundary applies here: `sync --prune` does not fetch during the prune
phase.

`sync --prune --dry-run` skips the sync phase entirely (nothing is
cloned, pulled or deleted) and prints the prune plan: a `planned` row
for each safe orphan, a `skipped` row for each unsafe one, and under
`--all` an `unavailable` row for each unreadable manifest. It exits
`0` and never prompts. `--dry-run` without `--prune` is a usage error
(exit `2`).

Known limitations:

- `-j` is a global cap, not host-aware. Pick values that fit your git
  remotes' SSH/HTTP limits.
- Ctrl-C cancels queued repo jobs instead of draining them; the command
  then waits only for in-flight git calls, which receive the same
  terminal interrupt.
- Clones made by older releases with plain `git clone --reference`
  still borrow objects from the bare cache (see
  `.git/objects/info/alternates`); deleting the cache can damage them.
  Run `git repack -a -d && rm .git/objects/info/alternates` in such a
  clone to make it independent.

**`--all --repo` semantics.** Under `--all`, `--repo` is a per-workspace
filter: workspaces whose manifests don't contain the requested
identifier emit one `unmatched` row per identifier and continue (so
`sync --all --repo deploy-config` traverses every workspace, syncing
the ones that have `deploy-config` and surfacing the rest as
`unmatched`). A typo is therefore visible across the run — e.g.
`sync --all --repo deploy-confg` produces an `unmatched` row in every
workspace, which is the discoverable signal. **Single-workspace
`--repo`** (no `--all`) keeps strict semantics — typos raise loudly
and abort the command.

### `status`

```bash
untaped workspace status [WS | --all] [--repo <repo>]...
                         [--dirty] [--behind] [--check]
                         [--format json|yaml|table|raw|pipe] [--columns ...]
```

Per-repo git snapshot: `branch`, its configured `upstream` (such as
`origin/main`; `null` when unset), `ahead`, `behind`, `modified`,
`untracked`, a `cloned` flag, the clone's absolute `target_path` (the
workspace directory on `unavailable` rows), and `action="status"` for
normal rows. The table shows `repo`, `cloned`, `branch`, `upstream`,
`ahead`, `behind`, `modified`, `untracked` and `detail` (led by
`workspace` under `--all`); json, yaml, raw and pipe keep every field.
Under `--all`, a registered workspace whose manifest cannot be read
emits one `action="unavailable"` row with `repo=""`, `cloned=false`,
and a `detail` message; single-workspace status remains strict.
A declared directory without its own `.git` reports `cloned=false` with
`detail="not a git repository"`; git is never run there, so it cannot
fall through to a repository enclosing the workspace (`sync` and
`branch apply` skip such directories with the same detail). A clone
whose `git status` fails keeps `cloned=true` and carries the error in
`detail` (and a structured `error`, as on a failed `sync` row).

Filters and checks:

- `--dirty` keeps only repos with uncommitted changes (`modified` or
  `untracked` above zero).
- `--behind` keeps only repos behind their upstream (`behind` above
  zero), as of the last fetch; `status` never fetches.
- Together, a repo matches either filter.
- The filters never hide a repo whose state could not be read: an
  uncloned repo, a failed `git status`, or an `unavailable` workspace.
- `--check` exits `3` when any repo is dirty or behind (only the
  `--dirty` / `--behind` condition when one is given), and `0` otherwise.
  It exits non-zero instead (`1`, or `5` when `git status` timed out) when
  any repo could not be inspected, since the check could not be answered. Without a filter it still prints every
  row.

```bash
# Repos with upstream commits you haven't pulled
untaped workspace status --all --behind --format raw --columns workspace --columns repo

# CI gate: fail when any repo has uncommitted work
untaped workspace status prod --dirty --check
```

### `foreach`

```bash
untaped workspace foreach [WS | --all] <cmd> [--repo <repo>... | --stdin]
                                [--timeout <seconds>]
                                [--parallel N]
                                [--continue-on-error | --ignore-errors]
                                [--format json|yaml|table|raw|pipe]
```

Run a shell command in every repo of a workspace. Default
`--format table` streams each repo's captured stdout / stderr with a
`[<repo>]` prefix as soon as that repo finishes (in completion order
under `--parallel`) — output is buffered per
repo, so chatty commands won't interleave but you also won't see
anything until each repo exits. `--format json|yaml|raw|pipe` emits one
`ForeachOutcome` row per repo (with `command`, `duration_s` and the
repo's `target_path`) for
piping into `jq` / `awk` or another command.

Pick the repos:

- `--repo <repo>` / `-r <repo>` (repeatable): only these repos.
- `--stdin`: repo names, one per line, or the `repo` field of a
  `workspace.repo`, `workspace.status` or `workspace.sync_outcome` pipe
  stream. Names are matched in `WS`; a record whose `workspace` field
  names another workspace exits `2`, as does any other record kind. An
  empty pipe (say, a `status --dirty` that matched nothing) runs nothing
  and exits `0`. `--stdin` cannot be combined with `--repo` or `--all`.
- `--all`: every registered workspace, in registry order, instead of
  `WS`. `--repo` then filters per workspace (an identifier no workspace
  declares is an error). Table output and the `failed in:` summary name
  repos as `<workspace>/<repo>`. A workspace whose manifest cannot be
  read is skipped with a warning. Fail-fast stops the later workspaces
  too.

A first argument that is not a registered workspace fails with a hint
to quote the command: `foreach build make` looks up a workspace named
`build`; write `foreach 'build make'`.

`--parallel N` / `-j N` defaults to the `workspace.parallel` setting, or
`min(8, 2 × CPUs)`, like `sync`; pass `-j 1` to run repos one at a time.

Child commands run with stdin closed (`DEVNULL`), so interactive
programs receive EOF instead of hanging the sweep. Each repo command
has a 600s timeout by default; raise it with `--timeout <seconds>` for
long builds or test suites. A timed-out command has return code `124`
and stderr includes `timed out after <Ns>s`; it otherwise follows the
same fail-fast / continue / ignore rules as any non-zero command.
Timeout cleanup is process-group best effort: deliberately daemonized
descendants or OS-level uninterruptible waits can delay the final pipe
drain after the timeout fires.

```bash
untaped workspace foreach prod 'git status -s'
untaped workspace foreach prod 'git status -s' --repo api --repo ui
untaped workspace foreach --all 'git pull --ff-only' --parallel 4

# Stash only the dirty repos
untaped workspace status prod --dirty --format pipe \
  | untaped workspace foreach prod 'git stash' --stdin
```

Three error-handling modes:

| Flag                   | Walks every repo? | Exit code            | Use when                                  |
| ---------------------- | ----------------- | -------------------- | ----------------------------------------- |
| *(default)*            | No — fail-fast    | non-zero on failure  | You want to stop and investigate.         |
| `--continue-on-error`  | Yes               | non-zero if any failed | You want every repo's outcome but still want CI to fail. |
| `--ignore-errors`      | Yes               | always `0`           | Inside `set -e` shell scripts where partial failure is fine. |

If both `--continue-on-error` and `--ignore-errors` are passed,
`--ignore-errors` wins on exit code (`--continue-on-error` is
redundant in that combination).

On `--format table`, a `failed in: <repos>` summary is written to
stderr whenever any repo failed — regardless of mode, so failures are
never silent. The summary is suppressed in `json|yaml|raw` since each
row's `returncode` carries the same information. In-flight commands
always run to completion; only queued work is cancelled on fail-fast.

Ctrl-C stops the sweep promptly, including under `--parallel`: every
running command's process group (each runs in its own session, so the
terminal's interrupt does not reach it) gets SIGTERM, then SIGKILL after
a short grace period, queued repos are cancelled rather than started,
and the command exits with the interrupt status.

### `path`

```bash
untaped workspace path <name>...                # one absolute path per name
untaped workspace path --stdin                  # read names from stdin
```

Pipe-friendly — pairs well with `cd "$(untaped workspace path prod)"`,
or to fan out paths:

```bash
untaped workspace list --format raw \
  | untaped workspace path --stdin
```

### `shell-init`

```bash
untaped workspace shell-init zsh                # or: bash, fish
```

Emits a shell snippet defining `uwcd <workspace>` and shell completion
for registered workspace names. Add it to your shell rc:

```bash
# in ~/.zshrc
eval "$(untaped workspace shell-init zsh)"

# then, anywhere:
uwcd <TAB>        # completes workspace names
uwcd prod          # cd ~/work/prod
```

### `edit`

```bash
untaped workspace edit [WS] [--editor <cmd>]
```

Opens the resolved workspace root in your editor. With no explicit
target, `edit` walks up from the current directory until it finds
`untaped.yml`, matching `repos`, `sync`, `status`, and `foreach`.
Honours `$VISUAL` then `$EDITOR`, overrideable with `--editor`. With
none of them set, `edit` fails with
`error: set $VISUAL or $EDITOR to use an external editor` (exit `4`), like
every other `edit` command. An editor that exits non-zero fails the command
with `error: editor exited with status N` (exit `1`).

## Recipes

### Morning routine across every workspace

```bash
untaped workspace sync --all
untaped workspace status --all --dirty --behind
```

Brings every registered workspace up to date, then flags any repo
that's behind upstream or has uncommitted changes.

### Pick a repo with `fzf` and run a command in just that one

```bash
repo="$(untaped workspace status prod --format raw --columns repo | fzf)"
untaped workspace foreach prod 'git log --oneline -10' --repo "$repo"
```

### Adopt a colleague's workspace

```bash
git clone git@github.com:acme/devops-manifests ~/manifests
untaped workspace import ~/manifests/prod.yml ~/work/prod --sync
```

### Adopt a directory you've already cloned by hand

```bash
mkdir -p ~/work/prod && cd ~/work/prod
git clone git@github.com:acme/api
git clone git@github.com:acme/web
untaped workspace adopt . --name prod
untaped workspace status prod                    # already populated
```

## Storage

By default, bare clones are cached at `~/.untaped/repositories`
(override with `untaped config set workspace.cache_dir <dir>`). Workspace
clones use `git clone --reference <bare> --dissociate`: the cached bare
saves network transfer, and the needed objects are then copied into the
clone, so every clone is self-contained and pruning, garbage-collecting,
or deleting the cache never breaks it. There are no branch conflicts of
the kind `git worktree` would introduce. The cache mirrors upstream
branches with `fetch --prune`; untaped also sets `gc.auto=0` and
`gc.pruneExpire=never` on it so clones made by older releases (which
still borrow cache objects) are not corrupted by automatic gc. Existing local clones do not touch the
bare cache during sync; they fetch their own `origin` refs and then
fast-forward or skip. Missing clones use the bare cache as the
reference source. A fresh bare clone is treated as already fresh, while
an existing bare is fetched at most once per bare cache path per sync
run, on demand, before reference clones use it. Concurrent untaped
processes can share the cache: each bare clone is created or fetched
under a lock file beside it (`<name>.git.lock`), so one process never
clones into, fetches, or removes another's partial clone. A ready bare
clone is used without taking the lock. A waiting process allows for a few
other processes' clone or fetch timeouts before its repo fails as locked,
and a cache where the lock file cannot be created fails the repo with
`could not lock bare cache`.

## See also

- [Configuration](../configuration.md) — `untaped config`, profile selection,
  and the YAML schema.
- [Pipes and record kinds](../reference/pipes.md) and [Exit codes](../reference/exit-codes.md).
- Run `untaped workspace <command> --help` for the current options and
  `untaped config list --format json` to inspect the active configuration.
