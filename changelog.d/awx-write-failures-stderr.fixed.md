awx: a failed `apply`, `patch`, `edit` or membership `add`/`remove` (such as
`groups hosts add`) now prints an `error: <Kind>/<name>: …` line, and a write
left unfinished without an error (skipped, or stopped by a conflict) prints a
`warning` line, so stderr and JSON diagnostics report them like `delete` does.
