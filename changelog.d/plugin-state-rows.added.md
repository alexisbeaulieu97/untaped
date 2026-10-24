A 12.0 plugin class keeps its state rows in its own stamped
`plugins/<plugin>/state.yml`, written under that file's lock:
`items = StateRows[Item](remove=Remove)` keys `Item` rows by the one field
marked `Key`, and a model with no such field makes the rows a list
(`all`, `append`, `replace_all`). A damaged row's error offers the `remove`
command with the row's key, and `StateRows[Item].per_project()` keeps rows
in a project's `.untaped/plugins/<plugin>/state.yml`. Composition refuses
rows it cannot key and rows named `untaped`.
