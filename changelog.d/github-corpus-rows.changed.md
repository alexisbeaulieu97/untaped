**Breaking (github):** `github.corpus_repo` rows from `github cache delete`
and `cache prune` now say `removed`, with the `disk_bytes` freed, or
`released`, with a new `kept` field naming what still uses the repo (another
plugin, a worktree added by hand) and `disk_bytes` 0. A row's `path` points
into the repo store under `git.store_dir`, and `cache worktree` checks refs
out under `~/.untaped/plugins/github/worktrees/`.
