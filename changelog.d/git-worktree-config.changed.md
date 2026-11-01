**Breaking (plugins):** `RepoStore.write_worktree_config` in `untaped_git.api`
takes no `profile` keyword: the credential helper line it writes names no
profile; the project the worktree is in picks it through its `profile:`, and
`UNTAPED_PROFILE` in the shell git runs from.
