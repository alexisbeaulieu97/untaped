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
  `~/.untaped/config.yml` mapping `name → path`. Just enough state to
  power `list`, `path <name>`, `--workspace X` lookups, and shell
  completions.

Manifests are checked into a shared directory or a git repo if you
want; the registry is local-only.

## Quick tour

```bash
untaped workspace init prod                     # new workspace at ~/.untaped/workspaces/prod
untaped workspace add git@github.com:acme/api --workspace prod  # add a repo
untaped workspace show --workspace prod                         # inspect manifest details
untaped workspace branch set main --workspace prod              # update manifest default branch
untaped workspace sync --workspace prod              # clone everything in the manifest
untaped workspace status --workspace prod            # per-repo git status
```

Workspace commands that read the registry or workspace profile settings
also accept command-local `--profile <name>`, so profile selection can
sit next to the command being run:

```bash
untaped workspace init prod --profile work
untaped workspace sync --workspace prod --profile work
untaped workspace status --workspace prod --profile work
```

The root form still works too, for example
`untaped --profile work workspace status --workspace prod`.

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
when `origin/<branch>` exists. If the branch is missing locally and on
`origin`, it creates a local branch from the current clean HEAD.
Subsequent `sync`s will not check out a different branch for you — if the
on-disk branch diverges from the manifest's target, `sync` skips that
repo with a warning, so a stale `defaults.branch` can't kidnap a repo
you've moved to a feature branch.

`repos[].name` is what shows up on disk under the workspace directory
and what you pass to `--only` / `remove`. Names and URLs must both be
unique within a manifest.

## Commands

### `list`

```bash
untaped workspace list                                      # tabular
untaped workspace list --profile work --format raw --columns name
```

Lists the central registry — every workspace `untaped` knows about by
name and path.

### `show`

```bash
untaped workspace show [--workspace <ws> | --path <dir>]
                       [--format json|yaml|table|raw] [--columns ...]
```

Show the manifest details for one workspace. Each repo produces one
row with the workspace name, manifest path, default branch, repo name,
repo URL, per-repo branch override, and effective target branch. Empty
manifests still emit a single summary row with `repo_count: 0`.

`show` reads `untaped.yml` only. It does not inspect git status or
remote state; use `workspace status` for live checkout data.

### `init`

> **Breaking change.** `init` previously took a path positional
> (`init <path> --name <name>`). It now takes the workspace **name**
> positionally with an optional `--path` override:
>
> ```bash
> # before:  untaped workspace init ~/work/prod --name prod
> # after:   untaped workspace init prod --path ~/work/prod
> ```
>
> The default location is `<workspace.workspaces_dir>/<name>`
> (`workspaces_dir` defaults to `~/.untaped/workspaces` and is
> profile-overridable). Update any shell aliases or scripts.

```bash
untaped workspace init <name> [--path <dir>] [--branch <default>] [--profile <name>]
```

Creates a new workspace named `<name>` and registers it. The default
location is `<workspace.workspaces_dir>/<name>` (the `workspaces_dir`
setting defaults to `~/.untaped/workspaces` and is profile-overridable).
Pass `-p / --path` to override the location for a one-off workspace
that lives elsewhere. Writes a starter `untaped.yml` in the directory.

### `adopt`

```bash
untaped workspace adopt <path> [--name <name>]
```

Initialise a workspace from a directory that already contains git
clones. Each immediate subdirectory containing `.git` is recorded in
the new manifest with its current `origin` URL and checked-out branch
(a detached HEAD becomes `branch: null`; clones missing an `origin`
emit a stderr warning and are skipped). The on-disk clones stay where
they are — `adopt` does **not** rewire them to share objects with the
bare cache; the cascade only links *new* clones via `git clone
--reference`.

```bash
git clone git@github.com:acme/api  ~/work/prod/api
git clone git@github.com:acme/web  ~/work/prod/web
untaped workspace adopt ~/work/prod --name prod
```

### `forget`

```bash
untaped workspace forget <name> [--prune] [--yes]
```

Remove a workspace from the central registry. The on-disk manifest and
clones are preserved by default — `forget` is the inverse of `init` /
`adopt`, not of `sync --prune`. Pass `--prune` to also `rmtree` the
workspace directory; pruning is refused (mirroring
`workspace remove --prune`) when any declared repo has uncommitted
changes. A missing manifest or missing directory is tolerated; the
registry entry is removed regardless.

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
                               [--sync]
untaped workspace add --stdin --workspace <ws>
```

Add one or more repo URLs to the workspace's manifest. Multiple URLs
may be passed positionally or via `--stdin`; `--branch` and
`--repo-name` apply uniformly to every URL in the batch (use one URL
per invocation for per-repo overrides). With `--sync`, also clone
the URLs that landed (a duplicate that fails to register won't try
to clone).

### `remove`

```bash
untaped workspace remove <repo>... [--workspace <ws> | --path <dir>]
                                  [--prune] [--yes]
untaped workspace remove --stdin [--workspace <ws> | --path <dir>]
```

Remove one or more repos from the manifest, identified by URL or
alias. `--prune` also deletes the local clone (refused if it has
uncommitted changes); `--yes` skips the confirmation prompt for
prune. With `--stdin`, reads repo identifiers one per line — works
nicely with `fzf`:

```bash
untaped workspace status --workspace prod --format raw --columns repo \
  | fzf -m \
  | untaped workspace remove --stdin --workspace prod
```

### `branch`

```bash
untaped workspace branch set <branch> [--workspace <ws> | --path <dir>]
                                [--repo <repo>] [--apply]
untaped workspace branch unset [--workspace <ws> | --path <dir>] [--repo <repo>]
untaped workspace branch apply [--workspace <ws> | --path <dir>] [--repo <repo>]
```

Set or unset branch metadata in `untaped.yml`. Without `--repo`, the
command updates `defaults.branch`; with `--repo`, it updates the
matching repo override by alias or URL.

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
one row per repo with `checkout`, `up-to-date`, or `skip`. Missing
clones and repos without a target branch are skipped. If the target
branch exists on `origin` but not locally, `branch apply` creates a
local tracking branch. If the target branch is missing locally and on
`origin`, it creates a local branch from the current clean HEAD.

### `sync`

```bash
untaped workspace sync [--workspace <ws> | --path <dir>]
                       [--only <repo>]... [--prune]
                       [--timeout <seconds>] [--all]
```

Reconcile each repo on disk with the manifest:

| Action       | When                                                      |
| ------------ | --------------------------------------------------------- |
| `clone`      | Repo is in the manifest but missing on disk.              |
| `pull`       | Repo exists; on the manifest's target branch; behind.     |
| `up-to-date` | Repo exists; nothing to do.                               |
| `skip`       | Repo exists but on a different branch (with a reason).    |
| `remove`     | Local clone is not in the manifest, and `--prune` is set. |
| `ignored`    | Local directory isn't a git repo.                         |
| `unmatched`  | `--all --only <repo>` was passed and `<repo>` isn't in this workspace's manifest — `repo` carries the unmatched identifier. |

`--only <repo>` limits sync to specific repos (repeatable);
`--all` runs sync against every workspace in the registry — handy as
a morning routine.

`--timeout <seconds>` caps every git invocation in this sync run, so a
hung remote can't strand a `--all` sweep. Defaults are 60s for
read-only ops and 600s for clone/fetch; passing `--timeout 30` caps
both at 30s (CI-friendly fail-fast).

**`--all --only` semantics.** Under `--all`, `--only` is a per-workspace
filter: workspaces whose manifests don't contain the requested
identifier emit one `unmatched` row per identifier and continue (so
`sync --all --only deploy-config` traverses every workspace, syncing
the ones that have `deploy-config` and surfacing the rest as
`unmatched`). A typo is therefore visible across the run — e.g.
`sync --all --only deploy-confg` produces an `unmatched` row in every
workspace, which is the discoverable signal. **Single-workspace
`--only`** (no `--all`) keeps strict semantics — typos raise loudly
and abort the command.

### `status`

```bash
untaped workspace status [--workspace <ws> | --path <dir>] [--all]
                         [--format json|yaml|table|raw] [--columns ...]
```

Per-repo git snapshot: `branch`, `ahead`, `behind`, `modified`,
`untracked`, and a `cloned` flag. Pipe-friendly:

```bash
# Repos with upstream commits you haven't pulled
untaped workspace status --all --format raw \
    --columns workspace --columns repo --columns behind \
  | awk '$3 > 0 { print }'
```

### `foreach`

```bash
untaped workspace foreach <cmd> [--workspace <ws> | --path <dir>]
                                [--parallel N]
                                [--continue-on-error | --ignore-errors]
                                [--format json|yaml|table|raw]
```

Run a shell command in every repo of a workspace. Default
`--format table` replays each repo's captured stdout / stderr with a
`[<repo>]` prefix once that repo finishes — output is buffered per
repo, so chatty commands won't interleave but you also won't see
anything until each repo exits. `--format json|yaml|raw` emits one
`ForeachOutcome` row per repo (with `command` and `duration_s`) for
piping into `jq` / `awk`.

```bash
untaped workspace foreach 'git status -s' --workspace prod
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

Emits a shell snippet defining `uwcd <workspace>` so you can jump to a
workspace by name. Add it to your shell rc:

```bash
# in ~/.zshrc
eval "$(untaped workspace shell-init zsh)"

# then, anywhere:
uwcd prod          # cd ~/work/prod
```

### `edit`

```bash
untaped workspace edit <name> [--editor <cmd>]
```

Opens the workspace directory in your editor. Honours `$VISUAL` then
`$EDITOR`, overrideable with `--editor`.

### Debugging silent completions

Tab completion is defensive: any error reading the registry (broken
YAML in `~/.untaped/config.yml`, a permission glitch, a malformed
entry) returns an empty list rather than a traceback. If a workspace
you expect to see is missing from the suggestions, set
`UNTAPED_COMPLETION_DEBUG=1` and re-trigger completion — a single
stderr line names the cause (`warning: completion: ConfigError: could
not parse …`). Strict `"1"` match; other values keep the silent
default. `untaped config list` and `untaped workspace list` are the
other diagnostics for the same class of failures.

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
git -C "$(untaped workspace path prod)/$repo" log --oneline -10
```

(`foreach` doesn't take a `--only` filter today; use the selected repo's
path directly, or use `--only` on `sync` instead.)

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
(override with `workspace.cache_dir` in your config). Workspace
clones use `git clone --reference` against the cached bare, so disk
and bandwidth are shared without the branch conflicts that
`git worktree` would introduce.

## See also

- [`untaped` configuration docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md)
  — `untaped config`, optional profile commands, and the YAML schema.
- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) — the
  optional AWX plugin.
- [AGENTS.md](../AGENTS.md) — internals (manifest vs registry split, the
  `GitRunner` boundary, sync state machine).
