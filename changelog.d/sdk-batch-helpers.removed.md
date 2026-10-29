**Breaking (plugins):** `untaped.sdk.batch_apply`, `BatchOutcome`, `finish`
and `resolve_each` are gone: they were 11.x's way to confirm a batch, report
each item's failure and pick the exit code. A command's `Confirm`,
`ctx.diag.failure`, failed rows, `Failed()` and `Matched()` do these now.
