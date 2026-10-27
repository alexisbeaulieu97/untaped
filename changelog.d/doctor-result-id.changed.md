**Breaking (plugins):** a doctor check's body leaves `DoctorResult(id=...)`
out: core names the row after the `DoctorCheck` it belongs to, and `id`
is keyword-only (`ok` is the first positional field). A body that passes an
id still gets a failed row when it is not its check's. `ok` must be a bool:
a row whose body returns another value fails.
