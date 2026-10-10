To keep the repo store somewhere other than `~/.untaped/plugins/git/store`,
set `git.store_dir` first, to a new directory rather than an old cache root.
Then run `untaped setup migrate-dirs`: it moves the repositories of the old
`github.cache_dir` and `workspace.cache_dir` (custom ones included) into the
store and deletes the old caches, while those settings still name them.
Last, run `untaped config migrate`, which deletes both settings and prints
the values they held.
