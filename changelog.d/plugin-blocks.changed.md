**Breaking (plugins):** `DoctorCheck`, `DirMigration` and
`DeprecatedBehaviour` are no longer dataclasses: everything after a check's
or migration's id (`title`, `run`, `online`, `preview`, `apply`) is
keyword-only, and two of them are equal only when they are the same object.
