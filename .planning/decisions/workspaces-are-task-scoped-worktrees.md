# workspaces are task-scoped worktrees

A workspace is one directory per task holding git worktrees of a shared bare
cache, created and archived as a unit, instead of a long-lived directory of
clones reconciled toward a manifest.

Rationale: work happens per ticket. Parallel agents need isolated checkouts
of the same repos, branches should be created where the work starts rather
than declared and reconciled, and finished work should leave no clones
behind. Worktrees share one object store per repo, so creating a workspace
of many repos is fast and cheap.

Constraints:

- The bare cache is load-bearing: worktrees reference it.
- Every cache write runs under a per-repo lock.
- Archiving never discards uncommitted, stashed or unpushed work without
  `--force`.

## Related decisions

- Builds on: [capabilities share code through a declared public module](capabilities-share-code-through-a-public-api-module.md)
- Builds on: [capability state lives in state.yml](capability-state-lives-in-state-yml.md)
