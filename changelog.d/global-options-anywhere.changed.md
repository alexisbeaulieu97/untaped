A global option (`--profile`, `--verbose`, `--quiet`, `--deprecated`) applies
to the whole run wherever it sits before `--`, also when the command declares
an option of the same name or reads the token as an argument; the command
still gets it too.
