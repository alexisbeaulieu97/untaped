`awx test` suite variables use the `values` plugin, as recipe inputs do: a
missing `--vars-file` fails when the flag is parsed (exit 2), a `list`
variable may set `items` to type its elements, a prompt reads `NAME for FILE
(description)`, and `untaped docs values guides vars` says how values are
given, parsed and asked. In `awx test list` rows, a variable without a
description has `""` instead of `null`.
