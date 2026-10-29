Delete `~/.untaped/state.yml` once you no longer run untaped 11.x on that
home: 12.0 reads nothing from it, nor from a project's `.untaped/state.yml`.
A plugin keeps its state as rows its plugin class holds,
`items = StateRows[Item]()`, keyed by the field marked `Key`; a string map
becomes rows of a model with a `Key` field and a value field.
