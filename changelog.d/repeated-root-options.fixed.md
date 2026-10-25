A global option (`--profile`, `--verbose`, `--quiet`, `--deprecated`) given
more than once works on every command instead of failing as an unknown
option: `-v -v`, or `-vv` in one cluster, turns on verbose (`-qq` quiet), and
`--profile a … --profile b` stacks both in order, `b` over `a`, as `--profile
a,b` does.
