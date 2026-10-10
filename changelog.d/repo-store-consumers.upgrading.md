Run `untaped setup migrate-dirs` first: it moves the repositories of the old
`github.cache_dir` and `workspace.cache_dir` (custom ones included) into the
repo store and deletes the old caches, while those settings still name
them. Then run `untaped config migrate`: it deletes both settings and prints
the values they held. If you kept a custom root, set `git.store_dir`
instead.
