**Breaking (plugins):** a record or diagnostic no longer declares its kind
with `class Widget(Record, kind="acme.widget")`, and `record_kinds`,
`DuplicateKindError` and `ForeignKindError` leave `untaped.sdk.records`
with the `foreign-kind` quarantine reason. Composition names every kind:
`<plugin>.<snake_case of the class>`, from the plugin that prints, reads or
lists the model.
