A command that called `batch_apply(..., destructive=True)` declares
`confirm: Confirm` and asks `self.confirm(question, preview=...)` before it
writes; a no prints `cancelled; no changes made` and exits 1. Where it
called `resolve_each(ids, fn)`, it reports each item's error with
`self.ctx.diag.failure(exc, item=id)` or yields a row whose `action` failed.
`finish(any_failed)` needs nothing once each failure is reported that way
(`raise Failed()` when no row or failure says so), and
`finish(..., predicate_hit=True)` becomes `raise Matched()`.
