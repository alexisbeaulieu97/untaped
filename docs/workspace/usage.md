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

Commands never discard local work: a repo they cannot update safely is
skipped with a reason, and deleting a clone always asks first. The
[packaged skill](../../src/untaped/capabilities/workspace/skills/untaped-workspace/SKILL.md)
and its references hold the per-command detail; `--help` and `--columns ?`
hold the options and fields.

## Set up

Create a workspace, or register one that already exists:

```bash
untaped workspace init prod                     # new workspace at ~/.untaped/workspaces/prod
untaped workspace adopt ~/work/prod --name prod # a directory you already cloned into
untaped workspace import ~/manifests/prod.yml ~/work/prod --sync  # a colleague's manifest
```

`adopt` registers an existing `untaped.yml` as is; in a directory without
one, it records each git clone it finds (URL and checked-out branch) in a
new manifest and leaves the clones in place.

Settings (`workspace.workspaces_dir`, `workspace.cache_dir`,
`workspace.parallel`) are in the
[configuration reference](../reference/config.md#workspace). New clones copy
their objects from the cache, so deleting the cache never breaks a clone.

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
```

`repos[].name` is the directory on disk and what you pass to `--repo` and
`repos remove`. Naming rules and how `adopt` and `import` fill the manifest
are in the skill's
[manifest reference](../../src/untaped/capabilities/workspace/skills/untaped-workspace/references/manifest.md).

### Choosing the workspace

Every command that acts on one workspace takes it as its first
positional argument, `WS`: a registered name (`prod`), a path inside a
workspace (`.`, `~/work/prod`, anything containing `/`), or nothing, for
the workspace containing the current directory. `sync`, `status` and
`foreach` take `--all` instead to act on every registered workspace.
`repos add` and `repos remove` need `WS` before the repos (`.` works), since
a lone argument is read as the workspace.

## Keep repos up to date

```bash
untaped workspace status --all --dirty --behind   # what needs attention
untaped workspace sync --all                      # a morning routine
```

`sync` clones missing repos, fetches the rest, and fast-forwards only the
clean ones on their manifest branch. Anything else (dirty, diverged, on
another branch, no upstream) is `skipped` with a reason, and the run exits
non-zero only when a row `failed`. It never switches branches, so a stale
`defaults.branch` can't move a repo you've put on a feature branch.

`status` never fetches, so "behind" is as of the last fetch or sync. As a CI
gate, `untaped workspace status prod --dirty --check` exits 3 when any repo
has uncommitted work.

Git never waits for credentials; set up an SSH agent or a credential helper
for private remotes. Row meanings, `--all` behaviour and timeouts are in the
[sync reference](../../src/untaped/capabilities/workspace/skills/untaped-workspace/references/sync.md).

## Add repos from elsewhere

`repos add --stdin` reads URLs or pipe records, for example a GitHub
inventory:

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

## Switch branches

```bash
untaped workspace branch set prod develop --repo api
untaped workspace branch set prod main --apply  # edit the manifest, then check out
```

`branch set` and `branch unset` only edit `untaped.yml`; `branch apply`
fetches and checks existing clones out, skipping dirty or diverged repos. A
branch that exists neither locally nor on `origin` is skipped, so a typo such
as `branch set mian` can't create a stray branch everywhere; pass `--create`
to create it from the current HEAD.

## Run a command in every repo

```bash
untaped workspace foreach prod 'git status -s'

# Stash only the dirty repos
untaped workspace status prod --dirty --format pipe \
  | untaped workspace foreach prod 'git stash' --stdin
```

Quote the command. By default `foreach` stops at the first failure;
`--continue-on-error` runs every repo and still fails, and `--ignore-errors`
always exits 0. See the
[foreach reference](../../src/untaped/capabilities/workspace/skills/untaped-workspace/references/foreach.md)
for selection, timeouts and output.

## Destructive commands

`repos remove --prune`, `forget --prune` and `sync --prune` delete clones;
`forget` alone only unregisters. Each previews its targets with `--dry-run`
and asks once; `--yes` skips the question. Without a terminal they need
`--yes` (else exit `2`), and declining exits `1` with nothing deleted
(`sync --prune` asks after the sync has run).

They never delete a clone with uncommitted, untracked, staged or stashed
work, or commits not on a remote-tracking branch. The check is offline and
does not see git-ignored files, so fetch first and mind local `.env` or build
files. What each prune deletes and how to recover are in the
[prune reference](../../src/untaped/capabilities/workspace/skills/untaped-workspace/references/prune.md).

## Jump between workspaces

```bash
cd "$(untaped workspace path prod)"

# in ~/.zshrc (or bash, fish): defines `uwcd <workspace>` with completion
eval "$(untaped workspace shell-init zsh)"
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
