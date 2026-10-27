**Breaking (users):** `untaped workspace` is now built on a 12.0 plugin class,
`WorkspacePlugin`. `workspace status` writes `workspace.repo_status` (was
`workspace.status`); the other kinds keep their names. Active workspaces are
kept as the `workspace.workspaces` rows (was `workspace.active`), and a
workspace's lock lives under `plugins/workspace/checkouts.locks/` in the
untaped home. The `untaped agents workspace lifecycle` page is gone: the
guide is `untaped workspace --help`. `workspace run --stdin` reads whole
records only, so a piped row missing a field is refused before anything
runs. Retired settings `workspace.cache_dir` and `workspace.protocol` are no
longer recognised. The credential helper line a worktree's git config gets
names no profile: the folder's project and `UNTAPED_PROFILE` pick it.
