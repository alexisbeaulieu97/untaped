# awx test failures name the responsible system; logs are read through events

Decision ID: `dec_01a0ee3c7ace70f6afb9ccbbac9d6d93`

An agent reading an `awx test` result must know whether to fix its playbook,
push its branch, fix credentials, or retry later. A flat failure reason and the
job's own (often empty) log could not tell it, and a failed project update
looked like a failed playbook.

- A case that does not pass carries one `failure`: the core `ErrorInfo`
  (`category`, `system`, `retryable`, `message`, `hint`), the same shape as
  every other failed row, plus `evidence`. The category selects the exit code
  as for any failure, and `test run` exits with the most severe case.
- `system` is one of eight `awx.*` values decided by pure, first-match rules
  over what AWX reports: the launch error, the job status, the update named in
  `job_explanation`, the traceback, and the failed task events. A failed
  update is blamed before any expectation, so a negative case whose playbook
  never ran cannot pass. Rules read events only once AWX has saved them, and
  never blame the playbook for events they could not read. An error that is
  not AWX's keeps its own system.
- Evidence comes from the responsible execution: the failed update's log and
  tasks, not the job's.
- Following a log reads only new job events and a tail only the newest ones;
  no poll downloads the whole log again, and no event is dropped or reordered
  while the job still runs.
- An unpushed `--scm-branch HEAD` stays a `git` `config` error (exit 4), as the
  core error model shipped it: it is found locally before any launch.

The flat result fields are removed without aliases, with untaped 9.0.
