# Workspaces, manifests and settings

## The manifest

`<workspace-dir>/untaped.yml` is the source of truth for what a workspace
holds. Illustration, with invented names:

```yaml
name: acme-prod          # registry name; the directory name when omitted
defaults:
  branch: main           # target branch for repos without their own
repos:
  - url: git@git.example.com:acme/api.git
    name: api            # directory under the workspace; derived from the URL when omitted
    branch: develop      # overrides defaults.branch for this repo
  - url: git@git.example.com:acme/web.git
```

- Repo names and URLs are unique within a manifest; names compare
  case-insensitively.
- A repo name, and a workspace name given to `init`, is a single path segment:
  not empty, `.` or `..`, no `/`, `\`, `:` or NUL, and not `untaped.yml`. A
  manifest that breaks this is rejected when loaded.
- A repo's target branch is its own `branch`, else `defaults.branch`. A new
  clone with neither checks out the remote's default branch.

## Which workspace a command acts on

- `WS` is a registered name, or a path inside a workspace: `.`, `..`, anything
  starting with `~` or containing `/`. Workspace names therefore cannot start
  with `~`.
- A path must exist; untaped walks up from it to the nearest `untaped.yml`, so
  unregistered workspaces work too.
- Omitted, `WS` is the workspace containing the current directory.
- `repos add`, `repos remove`, `foreach` and `branch set` read two or more
  positionals as the workspace, then the value. A lone argument is the
  workspace, so write `untaped workspace repos remove . api`, not
  `repos remove api`.
- There is no `--workspace` option. `init --path DIR` only chooses where a new
  workspace is created (default `<workspaces_dir>/<name>`).

## Creating and registering

- `init` and `import` refuse a name that is already registered, before
  writing anything.
- `adopt PATH` with an existing `untaped.yml` validates and registers it
  without rewriting it; `--name` registers it under another name.
- `adopt PATH` without a manifest records each immediate subdirectory holding
  `.git`, with its `origin` URL and checked-out branch (`branch: null` for a
  detached HEAD). A clone without `origin` is skipped with a warning. Clones
  stay where they are.
- `import SOURCE DEST` copies a manifest into a new directory; `--sync` clones
  its repos.
- `repos add` applies `--branch` and `--repo-name` to every URL given, so
  `--repo-name` with several URLs is a usage error. `--sync` clones only the
  URLs it added and prints sync rows instead of add rows.

## Settings

Set them with `untaped config set workspace.FIELD VALUE`:

| Setting | Purpose |
|---|---|
| `workspace.workspaces_dir` | where `init` creates workspaces |
| `workspace.cache_dir` | bare-repository cache that speeds up new clones |
| `workspace.parallel` | default number of repos `sync` and `foreach` work on at once (unset: `min(8, 2 × CPUs)`); `-j N` overrides it per run |

## The clone cache

New clones copy objects from the cache and keep no link to it, so deleting or
pruning `workspace.cache_dir` never breaks a clone. Existing clones fetch from
their own `origin`. `adopt` leaves existing clones untouched.

A clone may still borrow cache objects if `.git/objects/info/alternates`
exists in it. Before deleting the cache, make such a clone independent by
running `git repack -a -d && rm .git/objects/info/alternates` inside it.

## Other commands

- `path NAME...` prints absolute paths (`cd "$(untaped workspace path acme-prod)"`).
- `shell-init zsh|bash|fish` prints a snippet defining `uwcd NAME`, with
  completion, for the shell's rc file.
- `edit WS` opens the workspace in `$VISUAL` or `$EDITOR`; with neither set it
  exits 4.
