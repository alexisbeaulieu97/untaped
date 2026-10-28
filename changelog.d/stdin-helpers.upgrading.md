`read_identifiers(names, stdin=flag, id_field="id")` becomes a
`stdin: Stdin[Model]` field whose model marks its identifying field `Key`,
and `self.stdin.identifiers(names)` in `run`. That call refuses names and
`--stdin` together, as before. `resolve_text_input(value=..., file=...)`
becomes a `TextOrStdin` field (`-` reads stdin) or a `PathOrStdin` one for a
file.
