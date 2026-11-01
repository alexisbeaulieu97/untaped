Every command runs under its invocation's one run, so a block such as `Dir`
works under it. With an `UNTAPED_HOME` that doesn't resolve, a command that
reads the home (`plugin list`, `doctor`) exits 4 naming it; one that never
does (`untaped --help`, `--version`, `config --help`) still runs.
