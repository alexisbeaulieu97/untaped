`untaped recipe` rows of one kind now share one set of fields, writing as
null what used to be left out: `list`, `packs list`, `hooks list` and
`backups list` write the fields only their `get` fills, `hooks run` a
transform's or a validate's fields, `test` a passing case's `error`.
`packs get` adds `source`, `rev` and `commit`, `get` and `hooks get` add
`pack` and `name`, and `backups get` writes the `backups list` fields plus
`inputs` and `files` instead of the bundle's raw metadata.
