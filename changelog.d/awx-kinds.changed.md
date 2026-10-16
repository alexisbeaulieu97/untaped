awx rows write a field they used to leave out as `null`: `launch` and `sync`
rows write every field (`payload`, `detail` and the execution's, `error`
last), `delete` rows `detail`, `add`/`remove` rows `partial` and `detail`,
and `test run` expectations `node` on a job case. An
unknown `--columns` name is now a usage error (exit 2) on `jobs events`,
`jobs logs` (with `--follow` too), `launch`, `sync`, `apply`, `patch`,
`edit`, `delete`, `add`/`remove`, `workflow-templates nodes`, `usage` and the
`test` commands; `test run` checks it before launching anything. Resource,
job and unified-template rows in json and yaml lead with `id`, `name`,
`type` and the fields untaped reads, then AWX's other fields in its order.
`jobs events/logs --follow --columns '?'` lists the columns from the job's
rows without following. A test case failed by a broken inventory source is
now `failure.system` `awx.inventories` (was `awx.inventory`, the inventory
record's kind).
