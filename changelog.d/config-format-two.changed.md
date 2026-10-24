**Breaking (users):** `config.yml` is format 2: its top level is the base
(`default`), one section per plugin, and named profiles sit under `profiles:`.
Every write starts the file with `format_version: 2`. A non-empty
`config.yml` without the stamp, or stamped `format_version: 1`, was written by
11.x and is refused (exit 4) with one line naming the file: 12.0 has no
upgrade path, so delete it and run `untaped setup`, or keep untaped 11.1 for
that home. `profiles.default` and `active:` in a format 2 file are refused
too. `default` names the base and cannot be created, renamed or deleted.
