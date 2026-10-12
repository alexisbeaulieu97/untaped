Replace `update(f"{d}/{t}", fraction=d / t)` with `update(done=d, total=t)`
and `new_phase=True` with `phase="<name>"`; drop `write=` from
`report_error`; report a failure inside a task with
`ctx.diag.failure(exc, item=…)`; default an inner layer's handle to
`NO_PROGRESS`.
