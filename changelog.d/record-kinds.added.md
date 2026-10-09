`Record` is in the SDK: a record type declares its kind on the class,
`class Widget(Record, kind="acme-tools.widget")`, and `emit` reads it from the
rows. Kinds may start with a hyphenated plugin name, and a record type whose
fields would lose data in a JSON round trip fails at definition.
