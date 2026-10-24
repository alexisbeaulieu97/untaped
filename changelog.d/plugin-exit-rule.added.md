A 12.0 command raises `Failed()` after its rows when it failed in a way the
rows do not say (exit 1, nothing more printed) and `Matched()` when a check
matched (exit 3); a failure the rows counted wins over either. Rows already
yielded are printed before the error. `self.parallel.map(fn, items)` runs
`fn` on each item, `-j` at a time, and yields `(item, result)` pairs as they
finish (`ordered=True` keeps the input order), with `on_abort` for the work
in flight when it stops early and `while_running` for foreground work.
