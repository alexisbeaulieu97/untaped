Plugins can keep typed state rows with `StateRows` (experimental), listed on
`PluginSpec.stores`: each row is checked against its model, fields a newer
untaped added are written back as they were, and a damaged row stops the
command naming the file, the row and the fix.
`StateRows.per_project(...).of(project)` keeps a project's rows in its own
`state.yml`, as `StateMap(project=)` does for strings
([state.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/state.md)).
