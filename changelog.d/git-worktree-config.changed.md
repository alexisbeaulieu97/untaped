**Breaking (plugins):** `RepoStore.write_worktree_config` in `untaped_git.api`
takes no `profile` keyword: the credential helper line it writes names no
profile, and the folder's project and `UNTAPED_PROFILE` pick it.
