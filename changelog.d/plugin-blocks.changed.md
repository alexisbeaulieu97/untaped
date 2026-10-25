**Breaking (plugins):** `DoctorCheck` and `DeprecatedBehaviour` are no
longer dataclasses: everything after a check's id (`title`, `run`, `online`)
is keyword-only, and two of them are equal only when they are the same
object.
