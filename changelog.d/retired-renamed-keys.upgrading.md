`untaped config migrate` renames the old keys in `config.yml` but not the
`UNTAPED_*` variables, which 11.0 no longer reads: rename each one yourself.
`untaped doctor` names every retired variable still set and what replaces
it, and the configuration reference's "Renamed settings" table lists both
spellings.
