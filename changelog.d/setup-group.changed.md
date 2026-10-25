`untaped setup` is now built on core's own plugin class. Its commands,
options and exit codes are unchanged, and every `setup` command, `setup
prune` included, is marked `repairs`: it still runs under an invalid
`UNTAPED_FORMAT`, and the per-run skills check now skips it too. `setup
register` is marked experimental, since its record names the experimental
`RefHolder` and `RefTarget`. Under `--format json`, `yaml`, `pipe` or `raw`
the summary and closing lines on stderr now come before the rows on stdout,
and a setup screen that saved nothing prints `[]` rather than nothing.
