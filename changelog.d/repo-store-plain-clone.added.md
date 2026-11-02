A plugin opens a repo store repository with `plain_clone=True`
(`RepoStore.for_url`, `RepoStore.owned_by`) to give its worktrees the layout
of a clone (`origin/*`, the remote's tags, `origin/HEAD`), which only
workspace had; one plugin per repository holds it.
