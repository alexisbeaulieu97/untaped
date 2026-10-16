`awx jobs events --follow` and `jobs logs --follow` with `--columns '?'` list
the columns from the job's events or lines without following, and say no
columns are available when the job has none yet, as they do without
`--follow`.
