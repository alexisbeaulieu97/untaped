An unknown `--columns` name is now a usage error (exit 2) listing the valid
columns, as it already was for other record rows, on the root `auth status`,
`doctor`, `plugin list`, `plugin check`, `skills list`, `skills status` and
`setup` commands, on `github cache status`, `delete` and `prune` (before they
delete anything), on `github sweep`'s repo rows and its `--show files` and
`--show matches` rows (before it fetches), on `ansible source list`,
`ansible source get` and `ansible source-alias list`, and on `untaped recipe`
`list`, `get`, `validate`, `packs list`, `packs get`, `hooks list`,
`hooks get`, `backups get`, `test` and `hooks run` (before they run a hook or
write a golden); it used to only warn. Rows reporting a change now only warn,
since the change already happened, as do `doctor`'s and `setup plan`'s
tables, which show a command to type, the table of `github sweep`'s repo
rows, and the tables of `recipe get` and `recipe backups get`, which reshape
a field.
An unknown name is also a usage error on awx's `jobs events`, `jobs logs`,
`workflow-templates nodes`, `job-templates usage`, `workflow-templates usage`,
`test list` and `test run` rows (`test run` before it launches anything), and
on `<resource> export`'s documents printed with a `--format` other than `yaml`
(no `--out`).
