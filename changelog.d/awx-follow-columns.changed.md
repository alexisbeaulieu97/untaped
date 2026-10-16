`awx jobs events --follow` and `jobs logs --follow` with `--columns '?'` list
the columns from the job's events or lines without following; on a job with
none yet, they list the default columns, as they do without `--follow`.
