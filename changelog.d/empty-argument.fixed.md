An empty argument (`""`) reaches the command as given instead of being
dropped: `--name ""` is an empty name, and `profile create ""` says the name
is invalid rather than missing.
