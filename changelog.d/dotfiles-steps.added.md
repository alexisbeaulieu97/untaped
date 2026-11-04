`untaped dotfiles apply` runs a package's steps: a check (a command and
what it prints, a path, or a program on PATH), then the step's program or
source script when the check fails, then the check again; a step with only
a `fix` is reported. Steps show in the one preview, and nothing runs
before the confirmation. They follow only a policy the machine chose, else
`manual`, and `status` lists them (`kind: step` rows) as `pending`, `new`,
`applied`, `drifted`, `behind`, `unmet` or `orphan`, running only checks
that were approved; `status.json` counts `drifted` and `unmet`. A step's
own inputs are asked each run and never kept, a secret reaches the program
only on stdin, and what a run prints is shown with secrets masked.
`remove`, `unsubscribe` and turning an item on again forget its steps
without undoing them.
