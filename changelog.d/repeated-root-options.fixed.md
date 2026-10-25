A root option given more than once among a declared command's own options
works instead of failing as an unknown option: `-v -v` turns on verbose, and
`--profile a … --profile b` stacks both in order, `b` over `a`, as `--profile
a,b` does.
