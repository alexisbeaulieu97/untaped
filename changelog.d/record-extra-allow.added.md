A record that passes a service's answer through can set `extra="allow"`,
with its extras typed `__pydantic_extra__: dict[str, JsonValue]`: its row
keeps every field it was given, its typed fields first (its base's ahead of
a subclass's, so the identity leads) and a declared field the answer left out
still left out; its schema allows other fields, and an unknown `--columns`
name on it only warns. `untaped.sdk` exports `NOT_COUNTED`, which marks a
`Failure` field a command reports on its own, so `emit` does not count the
row as failed.
