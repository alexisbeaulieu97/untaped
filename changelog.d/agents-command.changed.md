`untaped agents` is now built on core's own plugin class, still experimental.
Its pages, arguments and exit codes are unchanged. Its `--help` lists `PATH`
under Options, as core's other commands list their arguments, and an unknown
option such as `agents --format json` is reported in text on stderr, as for
any command without `--format`, where 11.1 printed a JSON line.
