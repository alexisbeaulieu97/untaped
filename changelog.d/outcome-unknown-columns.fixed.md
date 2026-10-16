An unknown `--columns` name on rows reporting a change, such as those of
`config set`, `profile create` or `awx job-templates copy`, now only warns
and the rows still print; the command made the change, then exited 2 printing
none of its rows. `dotfiles subscribe`, `github cache worktree` and
`workspace status --fetch` now refuse the name before they clone, check out or
fetch anything, not after.
