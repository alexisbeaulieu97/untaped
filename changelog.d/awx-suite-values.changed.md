`awx test` suite variables use the `values` plugin, as recipe inputs do: a
missing `--vars-file` fails when the flag is parsed (exit 2), a key no suite
declares in a `--vars-file` is a usage error (exit 2, was 1) and reads
`unknown input: NAME; declared: ...`, a `list` given as text starting with
`[` is read as YAML, a `list` variable may set `items` to type its elements,
a prompt reads `NAME for FILE (description)`, and `untaped docs values guides
vars` says how values are given, parsed and asked. `awx test list` rows
always carry a variable's `description`.
