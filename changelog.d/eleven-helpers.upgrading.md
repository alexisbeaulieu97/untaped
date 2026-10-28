A plugin writes stderr through `ctx.diag`: `ui.message("info", x)` is
`ctx.diag.info(x)`, `"warning"` is `ctx.diag.warning(x)`, `"success"` (and
`ui.success(x)`) is `ctx.diag.success(x)`, and `"error"` is
`ctx.diag.failure(Failure.plain(x))`. `ui.progress(label)` is
`ctx.diag.progress(label)`, `report_error(exc)` is `ctx.diag.failure(exc)`,
and a row's error is `ctx.diag.failure(exc, item=...)`. `echo(x, err=True)`
is the call for its prefix: `error: ...` is a `Failure.plain`, `warning:`
and `hint:` are `ctx.diag.warning` and `ctx.diag.hint`, anything else is
`ctx.diag.info(x, unmuted=True)`. `ui.styled(x, err=True)` is
`ctx.diag.info(x, unmuted=True)` with any `tail` appended to the `Text`, or
`ctx.diag.relay(x)` for another tool's output. `project_root(path)` is
`checkout_root(path) or path.resolve()`, and a test calling
`check_conventions(name)` calls `untaped_lint.testing.assert_clean(name)`.
