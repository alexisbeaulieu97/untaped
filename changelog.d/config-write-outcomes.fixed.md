A config write that changes nothing in the file, such as setting a value a key
already holds or removing one from an empty `name:` profile, now leaves the
file untouched and reports unchanged, through `config set`, `config unset`,
`plugin rank`, setup and plugin settings writes.
