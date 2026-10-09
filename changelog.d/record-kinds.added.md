`Record` is in the SDK: a record type declares its kind on the class,
`class Widget(Record, kind="acme-tools.widget")`, and `emit` reads it from
the rows. A kind may start with a hyphenated plugin name.
