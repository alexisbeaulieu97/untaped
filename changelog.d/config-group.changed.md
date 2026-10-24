**Breaking (users):** `untaped config` is now built on core's own plugin
class. Its commands, output and exit codes are unchanged, but under
`--format json` or `yaml` its warnings and errors go to stderr as JSON lines
(`level`, `kind`, `message`), as for every 12.0 command, instead of
`warning: …` text. `config get` gains `--columns`.
