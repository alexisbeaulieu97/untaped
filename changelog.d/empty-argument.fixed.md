Arguments reach the command as given. An empty argument (`""`) is no longer
dropped: `--name ""` is an empty name, and `profile create ""` says the name
is invalid rather than missing. Data after `--` that looks like a short
cluster is no longer split: `-- -ab` gives `-ab`, not `-a` and `-b`, and
`-- -n5` gives `-n5`.
