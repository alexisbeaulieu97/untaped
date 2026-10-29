`untaped auth login` and `untaped config set --stdin` refuse a piped value
the same way: `no token received on stdin`, `stdin should hold the value on
one line`, both as an invalid value (exit 1). `auth login` exited 2 and
`config set` said `--stdin expects exactly one value`.
