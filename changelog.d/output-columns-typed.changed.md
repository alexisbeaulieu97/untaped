**Breaking (plugins):** a table column is a field reference. A string column
in `Output(columns=...)` or `self.output.columns` that names one of the
record's fields is refused when the command runs, with the `key(...)` to
write instead. A string still names a key no field declares: a `Spread` key,
or a key an `extra="allow"` record carries (an AWX row's `launch_type`).
`self.output.columns` is no longer a list: `run` changes it with `.add(...)`
(`first=True` to put them first), `.drop(...)` and `.show(...)`.
