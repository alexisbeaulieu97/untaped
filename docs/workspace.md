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
  power `list`, `path <name>`, `--name X` lookups, and shell
  completions.

Manifests are checked into a shared directory or a git repo if you
want; the registry is local-only.

## Quick tour

```bash
untaped workspace init prod                     # new workspace at ~/.untaped/workspaces/prod
untaped workspace add git@github.com:acme/api --name prod  # add a repo
untaped workspace sync --name prod              # clone everything in the manifest
untaped workspace status --name prod            # per-repo git status
```

If you `cd` into a workspace directory, the `--name` flag becomes
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
`defaults.branch` > the remote's HEAD. **The cascade is honoured at
clone time only.** Subsequent `sync`s will not check out a different
branch for you — if the on-disk branch diverges from the manifest's
target, `sync` skips that repo with a warning, so a stale
`defaults.branch` can't kidnap a repo you've moved to a feature branch.

`repos[].name` is what shows up on disk under the workspace directory
and what you pass to `--only` / `remove`. Names and URLs must both be
unique within a manifest.

## Commands

### `list`

```bash
untaped workspace list                          # tabular
untaped workspace list --format raw --columns name
```

Lists the central registry — every workspace `untaped` knows about by
name and path.

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
untaped workspace init <name> [--path <dir>] [--branch <default>]
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
untaped workspace import <source.yml> --path <dest> [--name <name>] [--sync]
```

Adopt an existing manifest into a new workspace directory. Useful when
a colleague shares a YAML file describing their workspace setup. Pass
`--sync` to clone everything immediately.

### `add`

```bash
untaped workspace add <url> [--name <ws>] [--path <ws-dir>]
                            [--branch <b>] [--repo-name <alias>]
                            [--sync]
```

Add a repo URL to the workspace's manifest. With `--sync`, also clone
the new repo right away.

### `remove`

```bash
untaped workspace remove <repo>... [--name <ws>] [--prune] [--yes]
untaped workspace remove --stdin --name <ws>
```

Remove one or more repos from the manifest, identified by URL or
alias. `--prune` also deletes the local clone (refused if it has
uncommitted changes); `--yes` skips the confirmation prompt for
prune. With `--stdin`, reads repo identifiers one per line — works
nicely with `fzf`:

```bash
untaped workspace status --name prod --format raw --columns repo \
  | fzf -m \
  | untaped workspace remove --stdin --name prod
```

### `sync`

```bash
untaped workspace sync [--name <ws> | --path <dir>]
                       [--only <repo>]... [--prune]
                       [--all]
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
untaped workspace status [--name <ws> | --path <dir>] [--all]
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
untaped workspace foreach <cmd> [--name <ws>]
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
untaped workspace foreach 'git status -s' --name prod
untaped workspace foreach 'git pull --ff-only' --name prod --parallel 4
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
untaped workspace path <name>                   # absolute path, single line
```

Pipe-friendly — pairs well with `cd "$(untaped workspace path prod)"`.

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
untaped workspace status --name prod --format raw --columns repo \
  | fzf \
  | xargs -I{} untaped workspace foreach 'git log --oneline -10' --name prod
```

(`foreach` doesn't take a `--only` filter today; pipe through `xargs`
or use `--only` on `sync` instead.)

### Adopt a colleague's workspace

```bash
git clone git@github.com:acme/devops-manifests ~/manifests
untaped workspace import ~/manifests/prod.yml --path ~/work/prod --sync
```

### Adopt a directory you've already cloned by hand

```bash
mkdir -p ~/work/prod && cd ~/work/prod
git clone git@github.com:acme/api
git clone git@github.com:acme/web
untaped workspace adopt . --name prod
untaped workspace status --name prod        # already populated
```

## Storage

By default, bare clones are cached at `~/.untaped/repositories`
(override with `workspace.cache_dir` in your config). Workspace
clones use `git clone --reference` against the cached bare, so disk
and bandwidth are shared without the branch conflicts that
`git worktree` would introduce.

## See also

- [`configuration.md`](./configuration.md) — `untaped config` /
  `untaped profile` and the YAML schema.
- [`awx.md`](./awx.md) — `untaped awx` commands.
- [`packages/untaped-workspace/AGENTS.md`](../packages/untaped-workspace/AGENTS.md) —
  internals (manifest vs registry split, the `GitRunner` boundary, sync
  state machine).
- [AGENTS.md](../AGENTS.md) — workspace-wide rules and recipes.
