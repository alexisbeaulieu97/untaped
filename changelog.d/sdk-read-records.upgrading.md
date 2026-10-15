Replace `read_records(accept_kinds=…)` with
`read_stdin_input(accept_kinds=…, what="records").records`; it is `None`
when stdin held bare lines, which `read_records` refused, so raise then.
