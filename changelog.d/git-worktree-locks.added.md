Plugins keeping worktrees in the repo store can lock them so pruning keeps a
moved or unmounted folder's entry (`RepoStore.worktree_add(lock_reason=...)`,
`RepoStore.lock_worktree`), reconnect one after a move
(`RepoStore.repair_worktree`) and list their worktrees under a folder
(`RepoStore.worktrees_under`).
