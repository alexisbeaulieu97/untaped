`untaped setup` is now built on core's own plugin class. Its commands,
options, records and exit codes are unchanged, and every `setup` command,
`setup prune` included, is marked `repairs`: it still runs under an invalid
`UNTAPED_FORMAT`, and the per-run skills check now skips it too. Agent pages
no longer tag `setup register` `(changes things)`.
