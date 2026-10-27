A 12.0 plugin class keeps its state rows in its own stamped
`plugins/<plugin>/state.yml`, written under that file's lock:
`items = StateRows[Item]()` keys `Item` rows by the one field marked `Key`,
and a model with no such field makes the rows a list (`all`, `append`,
`replace_all`). The command that removes a row marks itself
`removes = ITEMS`, and a damaged row's error offers it with the row's key.
`StateRows[Item].per_project()` keeps rows in a project's
`.untaped/plugins/<plugin>/state.yml`. Composition refuses rows it cannot
key, rows named `untaped`, a second command removing the same rows and a
`removes` naming rows the plugin class doesn't hold.
