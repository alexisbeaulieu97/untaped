Run `untaped config migrate`: it deletes `github.cache_dir` and
`workspace.cache_dir` and prints the values they held. If you kept a custom
root, set `git.store_dir` instead. Nothing reads the old cache directories
any more; delete them once no workspace from before the upgrade needs them.
