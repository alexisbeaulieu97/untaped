**Breaking (plugins):** a doctor check's body leaves `DoctorResult(id=...)`
out: core fills the row's id from the `DoctorCheck` it belongs to, and `id`
is keyword-only (`ok` is the first positional field). A body that passes an
id still gets a failed row when it is not its check's.
