`awx job-templates usage --stdin` and `awx workflow-templates usage/nodes
--stdin` look up piped `--format pipe` records by their `id`, so a template
name shared across organizations no longer picks the wrong template.
