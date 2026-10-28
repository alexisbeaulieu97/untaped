**Breaking (users):** `untaped awx` is now built on a 12.0 plugin class. Its
guides are the docstrings of its option bundles and records: `selecting` is
`untaped docs awx guides selection`, `launching` is `guides waiting` (and
`awx job-templates launch --help`), `documents` and `workflow-nodes` are
`untaped docs awx inputs document`, and `agent-profile` is `untaped docs awx
commands test`. Record kinds follow their classes: `awx.job` is
`awx.job_record`, `awx.event` `awx.job_event_row`, `awx.log`
`awx.job_log_line`, `awx.status` `awx.ping_status`, `awx.apply_outcome`
`awx.apply_outcome_row`, `awx.cancel_outcome` `awx.job_cancel_outcome`,
`awx.relaunch_outcome` `awx.job_relaunch_outcome`, `awx.test_result`
`awx.case_result`, `awx.test_case` `awx.suite_case` and `awx.template_usage`
`awx.workflow_usage`; `awx.resources` keeps its name. `--org` and
`--inventory-org` are gone (`--organization`, `--inventory-organization`).
An option a kind can't use is unknown there (exit 2), such as `--with-scm`
on `projects list` or `--verbosity` on `workflow-templates launch`.
`export --print-paths` is `export --format paths`, and `schema --format
yaml` is `schema --yaml`. `jobs events` and `jobs logs` stream: `json` is
one object per line and `yaml` one document per row, followed or not, and a
log row is its `line`, then its `job`, and an event row starts with its
`counter` and `event` (`-f raw` prints the counter; `-c
counter,event,host_name,task` gives 11.x's raw line). `jobs events` draws
AWX-style event lines in a table even without `--follow`; `--columns` gives
the column table. `test run --source-ref` prints its results after the
copies are torn down. Under `--yes` a write no longer
prints its preview (`--dry-run` shows it). `raw` prints each row's first
column unless `--columns` names more. A kind's `export --format json` prints
a list, even of one document. `test init` prints the suite's path as its
result.
