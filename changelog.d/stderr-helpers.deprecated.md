`ui.message`, `ui.success`, `ui.progress`, `report_error`,
`report_row_errors`, and `echo`/`ui.styled` with `err=True` are deprecated in
favour of `ctx.diag` and raise a `DeprecationWarning`; mypy reports them with
`enable_error_code = ["deprecated"]`, and they are removed in 12.0.
