12.0 starts from a new home, so nothing 11.x wrote is read: set the 12.0
settings in the new `config.yml`, and let each plugin fetch or build its
data again. A plugin that kept a file with `read_data_file` and
`write_data_file` names it once as `DataFile[Model].at(path)` (from
`untaped.sdk.storage`) and calls `read()` and `write(value)`: the files it
reads carry the stamp `write()` puts on them, so `unstamped="accept"` and
`legacy_stamps=` have no replacement.
