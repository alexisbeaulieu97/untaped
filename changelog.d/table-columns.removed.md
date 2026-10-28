**Breaking (plugins):** a record's `table_columns` class variable, and
`untaped.sdk.records.table_columns_of`, are gone. A record marks its default
table columns on its fields with `Column`. The rule that a listed record with
more than four fields declares default columns is gone too: a record that
marks none of its own fields shows every field.
