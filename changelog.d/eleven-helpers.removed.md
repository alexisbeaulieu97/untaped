**Breaking (plugins):** the stderr helpers deprecated in 11.x for
`ctx.diag` (`untaped.sdk.Diagnostics`): `UiContext.message`, `.success` and
`.progress`, `report_error`, `report_row_errors`, and the `err=` parameter of
`echo` and `UiContext.styled`. Also `untaped.sdk.project_root` (use
`checkout_root`) and `untaped.sdk.testing.check_conventions` (use
`check_plugin`, and `untaped lint` for the rules that read a plugin's files).
