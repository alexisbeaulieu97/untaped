# Deleting clones safely

Contents: the safety check · `sync --prune` · `repos remove --prune` ·
`forget --prune` · prompts and exit codes · recovery.

## The safety check

Before deleting a clone, untaped checks it for local work. A clone is unsafe
when it has any of:

- modified, staged or untracked files (`dirty working tree`);
- stash entries (`stash entries present`);
- commits on local branches, tags or HEAD that no remote-tracking ref
  contains (`local commits not reachable from any remote-tracking ref`).

It does not protect:

- git-ignored files (`.env`, build output, local config);
- commits that only stale remote-tracking refs still contain. The check never
  fetches, so run `untaped workspace sync WS` or `git fetch --prune` in the
  clone first when the remote may have dropped branches.

Ask the user before pruning a workspace that may hold ignored files they
need.

## sync --prune

- Orphans are the immediate subdirectories of the workspace that hold `.git`
  but are not declared in the manifest. Loose files and plain directories are
  never touched; symlinks are never followed.
- It runs after every sync job has finished. Unsafe orphans become `skipped`
  rows (`unsafe local state: <first reason>; +N more`), orphans git cannot
  inspect are `skipped` with `not a usable git repo`, and safe ones become
  `removed`.
- It asks only when at least one safe orphan exists.
- `untaped workspace sync WS --prune --dry-run` skips the sync and prints
  `planned` (would delete) and `skipped` (would keep) rows.
- Declining keeps the orphans; the sync itself has already run.

## repos remove --prune

- Removes each named repo from the manifest and deletes its clone.
- An unsafe clone is refused: that repo gets a `failed` row and stays in the
  manifest.
- If the manifest was updated but the delete failed, the row is `partial`
  with `pruned: false`, and the run exits 1.
- Its `--dry-run` lists the repos given as `planned` without running the
  safety check, so run `untaped workspace status WS --dirty` too.
- Without `--prune`, `repos remove` edits only the manifest and does not ask.

## forget --prune

- Deletes the declared clones, orphan clones, symlinks standing in for clones
  (never their targets) and `untaped.yml`. Loose files and non-git
  directories stay; the workspace directory goes only if it ends up empty,
  otherwise a `warning: left <path> in place` names what stayed.
- Any unsafe or uninspectable clone refuses the whole prune; nothing is
  deleted.
- A missing manifest is refused; a missing directory is tolerated.
- The registry entry is removed only after the prune succeeds, so a refusal
  or failure leaves the workspace registered.
- `untaped workspace forget NAME --prune --dry-run` runs the same checks, so it
  fails on an unsafe clone just as the real prune would, and otherwise lists
  every path it would delete.

## Prompts and exit codes

- Each command lists its targets and asks once, defaulting to No.
- `--yes` (`-y`) skips the question. `--dry-run` wins over `--yes`.
- Without a terminal, and without `--yes` or `--dry-run`, the command exits 2
  and changes nothing.
- Declining prints `cancelled; no changes made` and exits 1; the manifest,
  registry and files are unchanged.
- `--dry-run` without `--prune` is a usage error (exit 2) for `sync` and
  `forget`.

## Recovery

- A deleted clone passed the safety check, so its branches were on the
  remote: add the repo back if needed and run `untaped workspace sync WS`.
  Ignored files are gone.
- A refused repo or workspace is unchanged: resolve what the refusal names
  (commit and push, drop the stash) and rerun.
- A `partial` row left the clone on disk: delete it by hand, or let the next
  `sync --prune` treat it as an orphan.
- `forget` without `--prune` keeps every file; `untaped workspace adopt PATH`
  registers the directory again.
