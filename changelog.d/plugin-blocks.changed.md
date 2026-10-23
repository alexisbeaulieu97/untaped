**Breaking (plugins):** `DoctorCheck` and `DirMigration` are no longer
dataclasses: everything after the id (`title`, `run`, `online`, `preview`,
`apply`) is keyword-only, and two checks or migrations are equal only when
they are the same object.
