`untaped recipe` rows of one kind now share one set of fields, written as
null where a row has no value instead of left out: the `list` commands write
the fields only their `get` fills, `packs get` writes `manifest_name` (null
unless the pack was installed under another name, which `packs list` now
names too, with the pack's `project`), `hooks run` writes the other hook
kind's fields, and `test` writes `error` on every case. `packs get` adds
`source`, `rev` and `commit` and lists `path` before `recipes` and `hooks`;
`get` and `hooks get` add `pack` and `name`; `backups get` writes the
`backups list` fields (its `created_at` the bundle id's UTC time) plus
`inputs` and `files`, instead of the bundle's raw metadata.
