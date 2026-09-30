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

The [packaged skill](../../src/untaped/capabilities/workspace/skills/untaped-workspace/SKILL.md)
is the full reference for every command's behavior and edge cases.

## Set up

Create a workspace, or register one that already exists:

```bash
untaped workspace init prod                     # new workspace at ~/.untaped/workspaces/prod
untaped workspace init scratch --path ~/tmp/scratch --branch main
untaped workspace adopt ~/work/prod --name prod # a directory you already cloned into
untaped workspace import ~/manifests/prod.yml ~/work/prod --sync  # a colleague's manifest
untaped workspace list
```

`adopt` registers an existing `untaped.yml` as is; in a directory without
one, it records each git clone it finds (URL and checked-out branch) in a
new manifest and leaves the clones in place.

New workspaces go under `workspace.workspaces_dir`, and clones reuse objects
from a bare cache under `workspace.cache_dir`; `workspace.parallel` sets how
many repos `sync` and `foreach` work on at once. See the
[configuration reference](../reference/config.md#workspace). To use another
profile's settings, pass the root `--profile` option:

```bash
untaped --profile work workspace init prod
```

### The manifest — `untaped.yml`

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

`repos[].name` is the directory on disk and what you pass to `--repo` and
`repos remove`. It must be unique (case-insensitively) and a single path
segment.

### Choosing the workspace

Every command that acts on one workspace takes it as its first
positional argument, `WS`: a registered name (`prod`), a path inside a
workspace (`.`, `~/work/prod`, anything containing `/`), or nothing, for
the workspace containing the current directory. `sync`, `status` and
`foreach` take `--all` instead to act on every registered workspace.

## Add and remove repos

```bash
untaped workspace repos add prod git@github.com:acme/api git@github.com:acme/web
untaped workspace repos add prod git@github.com:acme/cli --branch develop --repo-name cli --sync
untaped workspace repos list prod
untaped workspace repos remove prod web
untaped workspace repos remove prod api --prune --dry-run
```

`repos add` and `repos remove` need `WS` before positional repos (`.` works).
`--stdin` reads URLs or repo names, one per line, or pipe records — for
example a GitHub inventory:

```bash
untaped github repos list --team acme/platform --format pipe \
  | untaped workspace repos add platform --stdin --sync
```

Or pick repos to drop with `fzf`:

```bash
untaped workspace status prod --format raw --columns repo \
  | fzf -m \
  | untaped workspace repos remove prod --stdin
```

`repos list` reads only `untaped.yml`; use `status` for live git state.
`repos remove --prune` also deletes the local clone (see
[Destructive commands](#destructive-commands)).

## Clone and update: `sync`

```bash
untaped workspace sync prod                     # clone missing repos, pull the rest
untaped workspace sync prod --repo api --repo web
untaped workspace sync --all                    # every registered workspace
untaped workspace sync prod -j 1 --timeout 30
```

Each repo gets one row:

| Action       | When                                                      |
| ------------ | --------------------------------------------------------- |
| `cloned`     | Repo is in the manifest but missing on disk.              |
| `pulled`     | Repo exists; on the manifest's target branch; behind its upstream. |
| `unchanged`  | Repo exists; nothing to do.                               |
| `skipped`    | Deliberately left alone: dirty, diverged, on a different branch, no upstream, not a git repository, or an unsafe orphan (with a reason). |
| `failed`     | A clone, fetch, status, or pull errored; `detail` names the step and git's error. |
| `removed`    | Local clone is not in the manifest, and `--prune` is set. |
| `planned`    | `--prune --dry-run`: a safe orphan clone `--prune` would remove. |
| `unmatched`  | `--all --repo <repo>`: `<repo>` isn't in this workspace's manifest. |
| `unavailable` | `--all`: a registered workspace whose manifest could not be read. |

`sync` never switches branches: a repo checked out on a branch other than
its manifest target is skipped, so a stale `defaults.branch` can't move a
repo you've put on a feature branch (use `branch apply` for that). Git never
waits for credentials; set up an SSH agent or a credential helper for
private remotes. The command exits non-zero when any row is `failed`, after
printing every row.

**`--prune`** also removes clones no longer declared in the manifest, after
syncing, when they hold no local work; unsafe ones are skipped with a
reason. `sync --prune --dry-run` skips the sync and prints only what the
prune would remove.

**`--all`** syncs every registered workspace — a handy morning routine. A
workspace whose manifest can't be read yields an `unavailable` row instead of
stopping the run, and `--repo` filters each workspace, so a name no
workspace declares shows up as an `unmatched` row in each.

## Check status

```bash
untaped workspace status prod
untaped workspace status --all --dirty --behind

# CI gate: fail when any repo has uncommitted work
untaped workspace status prod --dirty --check
```

`status` shows each repo's branch, upstream, ahead/behind counts and
modified/untracked files. It never fetches, so "behind" is as of the last
fetch. `--dirty` and `--behind` keep only repos that need attention;
`--check` exits `3` when any repo matches.

## Switch branches

```bash
untaped workspace branch set prod main          # manifest default branch
untaped workspace branch set prod develop --repo api
untaped workspace branch unset prod --repo api
untaped workspace branch apply prod             # check out the manifest's branches
untaped workspace branch set prod main --apply  # both steps at once
```

A new clone checks out the repo's own `branch`, else `defaults.branch`, else
the remote's HEAD. `branch set` and `branch unset` only edit `untaped.yml`;
`branch apply` fetches and checks existing clones out to their target
branch, skipping dirty or diverged repos. A branch that exists neither
locally nor on `origin` is skipped, so a typo such as `branch set mian`
can't create a stray branch everywhere; pass `--create` to create it from
the current HEAD.

## Run a command in every repo

```bash
untaped workspace foreach prod 'git status -s'
untaped workspace foreach prod 'git status -s' --repo api --repo ui
untaped workspace foreach --all 'git pull --ff-only' --parallel 4

# Stash only the dirty repos
untaped workspace status prod --dirty --format pipe \
  | untaped workspace foreach prod 'git stash' --stdin
```

Quote the command (`foreach 'make build'`). Each repo's output is printed,
prefixed `[<repo>]`, when that repo finishes. Commands get no stdin and a
600-second timeout (`--timeout` changes it). Choose how failures behave:

| Flag                   | Walks every repo? | Exit code            | Use when                                  |
| ---------------------- | ----------------- | -------------------- | ----------------------------------------- |
| *(default)*            | No — fail-fast    | non-zero on failure  | You want to stop and investigate.         |
| `--continue-on-error`  | Yes               | non-zero if any failed | You want every repo's outcome but still want CI to fail. |
| `--ignore-errors`      | Yes               | always `0`           | Inside `set -e` shell scripts where partial failure is fine. |

## Remove a workspace

```bash
untaped workspace forget prod                   # unregister; files stay
untaped workspace forget prod --prune --dry-run # list what --prune would delete
untaped workspace forget prod --prune
```

`forget` is the inverse of `init` / `adopt`. With `--prune` it also deletes
the clones and `untaped.yml` (never other files) and the directory if it
ends up empty.

## Destructive commands

`repos remove --prune`, `forget --prune` and `sync --prune` preview what they
will delete and ask once; `--yes` / `-y` skips the question and `--dry-run`
only previews. Without a terminal they need `--yes` (else exit `2`), and
declining exits `1` with nothing changed. They never delete a clone with
uncommitted, staged or stashed work, or commits not on a remote-tracking
branch. The check is offline, so fetch first if your remote-tracking refs
may be stale. See [Exit codes](../reference/exit-codes.md).

## Jump between workspaces

```bash
cd "$(untaped workspace path prod)"
untaped workspace list --format raw | untaped workspace path --stdin

# in ~/.zshrc (or bash, fish): defines `uwcd <workspace>` with completion
eval "$(untaped workspace shell-init zsh)"

untaped workspace edit prod                     # open in $VISUAL / $EDITOR
```

## Output

Every command prints rows you can reshape with `--format` and `--columns`;
see [Pipes and record kinds](../reference/pipes.md#workspace) and
[Exit codes](../reference/exit-codes.md).

## See also

- [Configuration](../configuration.md) and the
  [configuration reference](../reference/config.md#workspace).
- [GitHub](../github/usage.md), to find repos to add, and
  [Recipes](../recipe/usage.md), to change files across a workspace.
- Run `untaped workspace <command> --help` for the current options.
