An unknown `--columns` name is now a usage error (exit 2) listing the valid
columns on `ansible source list`, `get`, `set`, `patch` and `remove` and on
`ansible source-alias list`, `set` and `remove`, as it already was on
ansible's other rows; it used to only warn.
