Replace `read_records(accept_kinds=…)` with
`read_stdin_input(accept_kinds=…).records`, which is `None` when the input
was bare lines rather than a pipe stream.
