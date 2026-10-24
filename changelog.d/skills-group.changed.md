`untaped skills` is now built on core's own plugin class, and core's `untaped`
skill is declared by that class like any plugin's. Rows, kinds and exit codes
are unchanged. `skills remove` asks like core's other groups: the prompt reads
`Remove N skills?` after the paths, `--dry-run` lists the paths on stderr, a
removal that fails prints a `failed` row with its `error:` line, and agent
pages tag it `(asks first)`. A `--project-dir` that is not a directory says
`--project-dir: not a directory: PATH` (still exit 2).
