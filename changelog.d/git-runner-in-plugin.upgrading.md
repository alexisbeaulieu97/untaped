A plugin that runs git requires `untaped-git` and imports `run_git`,
`git_toplevel`, `GitResult` and `GitCommandError` from `untaped_git.api`, and
its tests import the fixtures from `untaped_git.testing`. `safe_path_segment`
comes from `untaped.sdk.storage`.
