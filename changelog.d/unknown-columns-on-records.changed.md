An unknown `--columns` name is now a usage error (exit 2) listing the valid
columns, as it already was for other record rows, on the root `auth`,
`config migrate`, `doctor`, `doctor fix`, `plugin`, `skills` and `setup`
commands, on `github cache`, on `github sweep`'s repo rows and its
`--show files` and `--show matches` rows, and on `ansible source list`,
`get`, `set`, `patch` and `remove` and `ansible source-alias list`, `set` and
`remove`; it used to only warn. `doctor`'s, `doctor fix`'s and `setup plan`'s
tables, which show a command to type, and the table of `github sweep`'s repo
rows still only warn.
The same holds for `untaped recipe` `apply`, `list`, `get`, `validate`,
`test`, `packs`, `hooks`, `backups get` and `backups prune`; the tables of
`apply`, `get` and `backups get`, which reshape a field, still only warn.
