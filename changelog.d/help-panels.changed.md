`untaped --help` lists plugins in their own Plugins panel, after untaped's own
Commands, and every help screen ends with a Global options panel
(`--profile`, `--verbose`, `--quiet`, `--deprecated`) instead of mixing them
into Parameters. `check_conventions` now fails a plugin help panel without a
`sort_key` below 100 (`unkeyed-panel`) or named like a core panel
(`reserved-panel`).
