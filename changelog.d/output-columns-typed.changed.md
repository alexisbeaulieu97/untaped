**Breaking (plugins):** a table column is a field reference. A string column
in `Output(columns=...)` or `self.output.columns` that names one of the
record's fields is refused, with the `key(...)` to write instead. So is a
string naming no field on a record that has no `Spread` field and doesn't
keep undeclared keys. A declared column is checked when the plugin loads (a
`field-type` composition problem), and a column `run` adds is checked when it
is added. A string still names a key no field declares: a `Spread` key, or a
key an `extra="allow"` record carries (an AWX row's `launch_type`).
`self.output.columns` is no longer a list: it doesn't index or compare equal
to one (`list(...)` gives the names), and `run` changes it with `.add(...)`
(`first=True` to put them first), `.drop(...)` and `.show(...)`.
