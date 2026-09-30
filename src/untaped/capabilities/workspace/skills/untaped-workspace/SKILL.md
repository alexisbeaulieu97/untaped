---
name: untaped-workspace
description: Use the `untaped workspace` command to manage local multi-repository git workspaces (register them, add or remove repos, clone and pull them together, check their status, switch branches, and run a command in every repo). Use when the user mentions a workspace, several repos at once, cloning or syncing many repos, dirty or behind repos, or running a command across repos.
---

# untaped workspace

A workspace is a directory whose `untaped.yml` manifest declares a set of git
repos. `untaped workspace` moves the clones toward the manifest without ever
discarding local work: anything it cannot do safely it skips with a reason,
and deleting a clone is always the user's decision.

This page is the map; the details ship next to it:

| File | Read it when |
|---|---|
| [references/manifest.md](references/manifest.md) | creating, adopting or importing a workspace, editing `untaped.yml`, choosing which workspace a command acts on, settings and the clone cache |
| [references/sync.md](references/sync.md) | reading `sync`, `status` or `branch apply` rows, working out why a repo was skipped or failed, running across `--all` workspaces |
| [references/prune.md](references/prune.md) | before any `--prune`: what gets deleted, what counts as unsafe, prompts and recovery |
| [references/foreach.md](references/foreach.md) | running a command in several repos: selecting repos, failure modes, output, timeouts |
| [references/output.md](references/output.md) | piping records between commands, or reading exit codes |

## Setup

- Settings live under `profiles.<name>.workspace`; the list of registered
  workspaces is state that `init`, `adopt`, `import` and `forget` maintain.
- Most commands take the workspace as the first positional argument, `WS`: a
  registered name or a path inside a workspace. Omitted, it is the workspace
  containing the current directory, which may not be the one the user means,
  so pass it explicitly.

## Commands

| When | Command |
|---|---|
| Start an empty workspace | `untaped workspace init NAME` |
| Clones already sit in one directory | `untaped workspace adopt PATH` |
| Someone shared a manifest file | `untaped workspace import SOURCE DEST --sync` |
| See what a workspace declares (reads the manifest only) | `untaped workspace repos list WS` |
| See live git state before changing anything | `untaped workspace status WS` |
| Declare or drop repos | `untaped workspace repos add WS URL`, `untaped workspace repos remove WS REPO` |
| Clone missing repos and fast-forward clean ones | `untaped workspace sync WS` |
| Move repos to another branch | `untaped workspace branch set WS BRANCH`, then `untaped workspace branch apply WS` |
| Run one shell command in each repo | `untaped workspace foreach WS 'CMD'` |
| Unregister a workspace, keeping its files | `untaped workspace forget NAME` |
| Print a workspace's directory | `untaped workspace path NAME` |

`--help` on any command lists its options; `--columns ?` lists a table's
fields.

## Workflows

### Set up a workspace

1. Create or register it with `init`, `adopt` or `import`.
2. `untaped workspace repos list WS`: check the repo names, URLs and branches.
3. `untaped workspace sync WS`: each row should be `cloned` or `unchanged`.
   Look up any `skipped` or `failed` row in
   [references/sync.md](references/sync.md).
4. `untaped workspace status WS`: every repo shows `cloned` and the expected
   branch.

### Update repos safely

1. `untaped workspace status WS --dirty --behind --format json` shows repos
   holding local work (`behind` is as of their last fetch).
2. `untaped workspace sync WS` fetches every clone, then fast-forwards only
   the clean ones that are on their target branch and have an upstream. It
   never checks out, merges or rebases.
3. Treat each `skipped` row as a question for the user (commit, stash,
   rebase, or `branch apply`), then sync again. Done when no row is `failed`
   and the remaining `skipped` rows are ones the user accepts.

### Switch branches

1. `untaped workspace branch set WS BRANCH` (add `--repo REPO` for one repo)
   edits only the manifest.
2. `untaped workspace branch apply WS` checks out clean clones: rows are
   `checked_out` or `unchanged`. A `skipped` row reading
   `branch not found locally or on origin` is usually a typo; pass `--create`
   only when the user wants a new branch.

## Deleting clones

Three commands delete files; each lists its targets and asks once.

| Command | Deletes |
|---|---|
| `untaped workspace sync WS --prune` | clones in the workspace directory that the manifest no longer declares, after syncing |
| `untaped workspace repos remove WS REPO --prune` | the named repos' clones, as they leave the manifest |
| `untaped workspace forget NAME --prune` | every clone, `untaped.yml`, and the directory if nothing else remains |

1. Preview with the same command plus `--dry-run`, scoped to the named workspace
   and repos (`sync --prune --dry-run` does not sync).
2. Show the user the paths it would delete and any clone refused as unsafe.
3. Rerun with `--yes` only after the user approves. Without a terminal and
   without `--yes` the command exits 2 and changes nothing; declining exits 1.
4. The safety check is offline and ignores git-ignored files; read
   [references/prune.md](references/prune.md) for what it protects and how to
   recover. `repos remove` without `--prune` keeps the clone, which the next
   `sync --prune` treats as an orphan.

## Pitfalls

- `status` never fetches; `sync` does. Compare `behind` counts only after a
  sync or a fetch.
- `sync` leaves a repo on a branch other than its manifest target alone
  (`skipped`); that is not an error.
- Git never prompts for credentials, so a remote that needs them fails that
  repo. Use an SSH agent or a credential helper.
- Quote the `foreach` command (`'make build'`); it stops at the first failure
  unless told otherwise.
