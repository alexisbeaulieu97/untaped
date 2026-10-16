The deprecated stderr helpers now say when they go: `report_error`,
`report_row_errors`, `UiContext.message`, `.success` and `.progress` warn
"will be removed in 12.0" and name their replacement, and `echo(err=)` and
`UiContext.styled(err=)` warn whenever `err=` is passed, `err=False` included,
since 12.0 removes the parameter. `lint.warnings-warn` no longer lets a
`warnings.warn(..., DeprecationWarning)` through: a Python API deprecates
through its mark.
