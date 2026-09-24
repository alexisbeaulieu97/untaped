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
  power `list`, `path <name>`, and `--workspace X` lookups.

Manifests are checked into a shared directory or a git repo if you
want; the registry is local-only.

## Quick tour

```bash
untaped workspace init prod                     # new workspace at ~/.untaped/workspaces/prod
untaped workspace add git@github.com:acme/api --workspace prod  # add a repo
untaped workspace get --workspace prod                          # inspect manifest details
untaped workspace branch set main --workspace prod              # update manifest default branch
untaped workspace sync --workspace prod              # clone everything in the manifest
untaped workspace status --workspace prod            # per-repo git status
```

To use another profile's settings (for example its `workspace.workspaces_dir`),
pass the root `--profile` option right after `untaped` or at the end of the
command:

```bash
untaped --profile work workspace init prod
untaped workspace sync --workspace prod --profile work
```

If you `cd` into a workspace directory, the `--workspace` flag becomes
optional — most commands walk up from the current directory looking
for an `untaped.yml`.

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
and what you pass to `--repo` / `remove`. Names and URLs must both be
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

### `get`

```bash
untaped workspace get [--workspace <ws> | --path <dir>]
                      [--format json|yaml|table|raw|pipe] [--columns ...]
```

Show the manifest details for one workspace. Each repo produces one
`workspace.repo` row with the workspace name, manifest path, default
branch, repo name, repo URL, per-repo branch override, effective target
branch, and the clone's absolute `target_path`. Empty manifests still
emit a single `workspace.repo.summary` row with `repo_count: 0` and no
`target_path`.

`get` was called `show` before; `show` still works as a hidden,
deprecated alias that prints a warning.

`get` reads `untaped.yml` only. It does not inspect git status or
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
untaped workspace forget <name> [--prune] [--yes] [--format ...] [--columns ...]
```

Remove a workspace from the central registry. The on-disk manifest and
clones are preserved by default — `forget` is the inverse of `init` /
`adopt`, not of `sync --prune`. Pass `--prune` to also delete what
untaped manages in the workspace directory; the command previews the
workspace name and its absolute path and confirms the destructive
operation unless `--yes` / `-y` is passed. A declined prompt exits `1`
(`cancelled; no changes made`) without changing registry state or
files. A forgotten workspace produces one `workspace.forget_outcome` row
(`name`, `action: forgotten` or `pruned`, `target_path`).

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
the imported manifest — same scope as `add --sync`).

### `add`

```bash
untaped workspace add <url>... [--workspace <ws>] [--path <ws-dir>]
                               [--branch <b>] [--repo-name <alias>]
                               [--sync] [--format ...] [--columns ...]
untaped workspace add --stdin --workspace <ws>
```

Add one or more repo URLs to the workspace's manifest. Multiple URLs
may be passed positionally or via `--stdin`; `--branch` and
`--repo-name` apply uniformly to every URL in the batch (use one URL
per invocation for per-repo overrides). `--repo-name` with more than
one URL is a usage error (exit `2`). Each added repo produces one
`workspace.add_outcome` row (`workspace`, `repo`, `url`, `branch`,
`action: added`, `target_path`). With `--sync`, also clone the URLs
that landed (a duplicate that fails to register won't try to clone);
the command then prints the `workspace.sync_outcome` rows instead of
the add rows.

`--stdin` reads one URL per line, or a `--format pipe` stream of
`github.repo`, `github.repo_hit` or `github.sweep_repo` records (their
`clone_url`, else `url`) or `workspace.repo` records (their `url`), so
`untaped github search repos --org acme --format pipe | untaped workspace
add --stdin` works. A pipe record of any other kind exits `2`.

### `remove`

```bash
untaped workspace remove <repo>... [--workspace <ws> | --path <dir>]
                                  [--prune] [--yes] [--dry-run]
                                  [--format ...] [--columns ...]
untaped workspace remove --stdin [--workspace <ws> | --path <dir>]
```

Remove one or more repos from the manifest, identified by URL or
alias. `--prune` also deletes the local clone after the SDK batch
preview and confirmation, unless `--yes` / `-y` is passed. A declined
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
untaped workspace status --workspace prod --format raw --columns repo \
  | fzf -m \
  | untaped workspace remove --stdin --workspace prod
```

### `branch`

```bash
untaped workspace branch set <branch> [--workspace <ws> | --path <dir>]
                                [--repo <repo>] [--apply [--create]]
untaped workspace branch unset [--workspace <ws> | --path <dir>] [--repo <repo>]
                               [--format ...] [--columns ...]
untaped workspace branch apply [--workspace <ws> | --path <dir>] [--repo <repo>]...
                               [--create]
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
untaped workspace branch set main --workspace prod
untaped workspace branch apply --workspace prod

# or do both steps in one command
untaped workspace branch set main --workspace prod --apply
```

`branch apply` fetches first, refuses dirty or diverged repos, and emits
one row per repo (with the clone's `target_path`) whose `action` is
`checked_out`, `unchanged`, `skipped`, or `failed`
(a fetch, status, or checkout error; the command then exits `1`). Missing
clones and repos without a target branch are skipped. If the target
branch resolves to a commit on `origin` but not locally, `branch apply`
creates a local tracking branch. If the target branch is missing locally
and no usable `origin` ref exists, the repo is skipped with `branch not
found locally or on origin`, so a typo such as `branch set mian` cannot
create a stray branch in every repo. Pass `--create` (to `branch apply`
or `branch set --apply`) to create it from the current clean HEAD
instead.

### `sync`

```bash
untaped workspace sync [--workspace <ws> | --path <dir>]
                       [--repo <repo>]... [--prune [--yes]]
                       [--timeout <seconds>] [--parallel N] [--all]
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
| `unmatched`  | `--all --repo <repo>` was passed and `<repo>` isn't in this workspace's manifest — `repo` carries the unmatched identifier. |
| `unavailable` | `--all` hit a registered workspace whose manifest could not be read — `repo` is empty and `detail` explains the manifest failure. |

Every row carries an absolute `target_path`: the repo's clone
directory, or the workspace directory for `unmatched` and `unavailable`
rows. Earlier releases spelled these actions `clone`, `pull`,
`up-to-date`, `skip` and `remove`.

A `pulled` row fast-forwards the checked-out branch to its configured upstream
(`@{upstream}`), which need not be `origin/<same name>`. A branch with
no upstream is skipped with `no upstream` rather than reported as up to
date.

`sync` exits `1` when any row is `failed` (after printing every row), so
scripts and CI notice a clone or fetch that did not happen; `skipped` rows
alone keep exit `0`. `add --sync` and `import --sync` follow the same
rule.

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
across every selected repo, not per workspace. The value is clamped to
`2 * os.cpu_count()` with a stderr warning when needed; a value below
`1` is a usage error (exit `2`). Default sync is still serial.

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
without `--yes` it prints the sync rows and exits `2` with an error
instead of deleting; declining keeps every orphan. There is nothing to
confirm, and no `--yes` needed, when no safe orphans exist. Safety is
re-checked right before each delete. The same local remote-tracking ref
boundary applies here: `sync --prune` does not fetch during the prune
phase.

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
untaped workspace status [--workspace <ws> | --path <dir>] [--all]
                         [--repo <repo>]...
                         [--format json|yaml|table|raw|pipe] [--columns ...]
```

Per-repo git snapshot: `branch`, `ahead`, `behind`, `modified`,
`untracked`, a `cloned` flag, the clone's absolute `target_path` (the
workspace directory on `unavailable` rows), and `action="status"` for
normal rows.
Under `--all`, a registered workspace whose manifest cannot be read
emits one `action="unavailable"` row with `repo=""`, `cloned=false`,
and a `detail` message; single-workspace status remains strict.
A declared directory without its own `.git` reports `cloned=false` with
`detail="not a git repository"`; git is never run there, so it cannot
fall through to a repository enclosing the workspace (`sync` and
`branch apply` skip such directories with the same detail). A clone
whose `git status` fails keeps `cloned=true` and carries the error in
`detail`.
Pipe-friendly:

```bash
# Repos with upstream commits you haven't pulled
untaped workspace status --all --format raw \
    --columns workspace --columns repo --columns behind \
  | awk '$3 > 0 { print }'
```

### `foreach`

```bash
untaped workspace foreach <cmd> [--workspace <ws> | --path <dir>]
                                [--repo <repo>]...
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
untaped workspace foreach 'git status -s' --workspace prod
untaped workspace foreach 'git status -s' --workspace prod --repo api --repo ui
untaped workspace foreach 'git pull --ff-only' --workspace prod --parallel 4
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
untaped workspace edit [--workspace <ws> | --path <dir>] [--editor <cmd>]
```

Opens the resolved workspace root in your editor. With no explicit
target, `edit` walks up from the current directory until it finds
`untaped.yml`, matching `get`, `sync`, `status`, and `foreach`.
Honours `$VISUAL` then `$EDITOR`, overrideable with `--editor`. An
editor that exits non-zero fails the command with
`error: editor exited with status N` (exit `1`).

## Recipes

### Morning routine across every workspace

```bash
untaped workspace sync --all
untaped workspace status --all --format raw \
    --columns workspace --columns repo --columns behind --columns modified \
  | awk '$3 > 0 || $4 > 0 { print }'
```

Brings every registered workspace up to date, then flags any repo
that's behind upstream or has uncommitted changes.

### Pick a repo with `fzf` and run a command in just that one

```bash
repo="$(untaped workspace status --workspace prod --format raw --columns repo | fzf)"
untaped workspace foreach 'git log --oneline -10' --workspace prod --repo "$repo"
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
untaped workspace status --workspace prod        # already populated
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
run, on demand, before reference clones use it.

## See also

- [Configuration](../configuration.md) — `untaped config`, profile selection,
  and the YAML schema.
- [Pipes and record kinds](../reference/pipes.md) and [Exit codes](../reference/exit-codes.md).
- Run `untaped workspace <command> --help` for the current options and
  `untaped config list --format json` to inspect the active configuration.
