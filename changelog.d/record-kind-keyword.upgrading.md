Drop `kind=` from each record and diagnostic class, and list a model no
command names in its plugin class's `records` (a diagnostic a command
emits, a record a provider's bridge reads). Name the class after the kind it
should keep: `kind="acme.repo_row"` is `class RepoRow(Record)`. A column
coloured by its name alone (`status`, `action`, `result`, `state`,
`job_status`) now needs a `Status(...)` on its field.
