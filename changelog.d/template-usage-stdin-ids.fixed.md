awx: `job-templates usage --stdin` and `workflow-templates usage|nodes
--stdin` look up piped `--format pipe` records by their `id` (`--by-id` now
applies only to bare lines; a record without an `id` is an error), so a
template name shared across organizations no longer picks the wrong template.
