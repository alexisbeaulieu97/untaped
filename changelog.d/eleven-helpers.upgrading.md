A plugin writes stderr through `ctx.diag`: `ui.message("info", x)` is
`ctx.diag.info(x)`, `ui.success(x)` is `ctx.diag.success(x)`,
`ui.progress(label)` is `ctx.diag.progress(label)`, `report_error(exc)` is
`ctx.diag.failure(exc)`, a row's error is `ctx.diag.failure(exc, item=...)`,
and `echo(x, err=True)` or `ui.styled(x, err=True)` is
`ctx.diag.info(x, unmuted=True)`. `project_root(path)` is
`checkout_root(path)`, and a test calling `check_conventions(name)` calls
`check_plugin(name)` and `untaped_lint.testing.assert_clean(name)`.
