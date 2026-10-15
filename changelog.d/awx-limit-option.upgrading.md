Drop `--limit 0` from `awx <resource> list` and `awx unified-templates list`
calls, which list everything without `--limit`. For more than 20 jobs, pass
`awx jobs list --limit N` with the number you need.
