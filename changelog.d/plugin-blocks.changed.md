**Breaking (plugins):** `DoctorCheck` and `DeprecatedBehaviour` are no
longer dataclasses: their arguments (`title`, `run`, `online`) are
keyword-only, and two of them are equal only when they are the same
object.
