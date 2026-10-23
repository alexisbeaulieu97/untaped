Pass a `DoctorCheck`'s or `DirMigration`'s `title`, `run`, `preview` and
`apply` by keyword (`DoctorCheck("acme.api", title="acme API", run=probe)`),
and build a changed copy with a new call instead of `dataclasses.replace`.
