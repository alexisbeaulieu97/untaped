A 12.0 plugin class keeps its state rows in its own stamped
`plugins/<plugin>/state.yml`, written under that file's lock:
`items = StateRows[Item]()` keys `Item` rows by the one field marked `Key`,
and a model with no such field makes the rows a list (`all`, `append`,
`replace_all`). The command that removes a row marks itself
`removes = ITEMS` and takes the key under the `Key` field's name, as its
first positional or as an option (`--id KEY`); a damaged row's error offers
it with the row's key. List rows have one remover too.
`StateRows[Item].per_project()` keeps rows in a project's
`.untaped/plugins/<plugin>/state.yml`. Composition refuses rows it cannot
key, rows named `untaped`, a second command removing the same rows, a
`removes` naming rows the plugin class doesn't hold and a remover with no
field named like the rows' key.
