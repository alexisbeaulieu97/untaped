**Breaking (users):** `untaped workspace` is now built on a 12.0 plugin class,
`WorkspacePlugin`. `workspace status` writes `workspace.repo_status` (was
`workspace.status`); the other kinds keep their names. The workspace
lifecycle guide is on `untaped workspace --help` and `untaped docs
workspace`. `workspace run --stdin` reads whole records only, so a piped row
missing a field is refused before anything runs. Retired settings
`workspace.cache_dir` and `workspace.protocol` are no longer recognised. The
credential helper line a worktree's git config gets names no profile: the
folder's project picks it (its `profile:`), and `UNTAPED_PROFILE`.
`workspace create` and `workspace add` print their rows only: the
workspace's path is no longer the last stdout line, so `cd "$(untaped -q
workspace create ...)"` no longer works.
