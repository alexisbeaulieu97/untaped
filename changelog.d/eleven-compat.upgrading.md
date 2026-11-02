12.0 starts from a new home, so nothing 11.x wrote is read: set the 12.0
settings in the new `config.yml`, and let each plugin fetch or build its
data again. A plugin that passed `unstamped="accept"` or `legacy_stamps=`
to `read_data_file` drops them: the files it reads carry the stamp
`write_data_file` puts on them.
