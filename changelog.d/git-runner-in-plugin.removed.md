**Breaking (plugins):** `untaped.sdk.git` and `untaped.sdk.testing.git` are
gone: running git belongs to the git plugin, so `run_git`, `git_toplevel`,
`GitResult` and `GitCommandError` come from `untaped_git.api`, and the
repo-store fixtures from `untaped_git.testing`.
