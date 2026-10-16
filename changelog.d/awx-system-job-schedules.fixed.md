`untaped awx export` reads a management job's schedule (AWX's stock cleanup
schedules) instead of failing on its parent: the parent is written as
`{kind: SystemJobTemplate, name: ...}`, global, and `apply` creates the
schedule under that system job template again.
