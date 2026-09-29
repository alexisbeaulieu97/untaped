# awx test failures name the responsible system; logs are read through events

Decision ID: `dec_01a0ee3c7ace70f6afb9ccbbac9d6d93`

An agent reading an `awx test` result must know whether to fix its playbook,
push its branch, fix credentials, or retry later. A flat `failure_reason` and
the job's own (often empty) log could not tell it, and a failed project update
looked like a failed playbook.

- A case that does not pass carries one `failure`: the core `ErrorInfo`
  (`category`, `system`, `retryable`, `hint`) extended with `evidence`, its
  message serialized as `summary`. It is not a second error shape: the
  category selects the exit code exactly as for any other failure, and each
  case's failure is noted, so `test run` exits with the most severe case.
- `system` is one of eight `awx.*` values decided by pure, first-match rules
  over what AWX already reports: the launch error, the job status,
  `job_explanation` (`Previous Task Failed: {json}` names the failed
  project or inventory update), `result_traceback`, and the failed task
  events. A failed inventory update is the environment (`config`, exit 4);
  unreachable hosts, a job stuck pending and a controller-side `error` are
  temporary (`unavailable`, exit 5).
- Evidence comes from the responsible execution: the failed update's log tail
  and failed tasks, not the job's.
- Logs are followed through job events (`counter__gt`, ANSI stripped) and a
  tail reads only the newest events (`order_by=-counter`, small `page_size`),
  never re-downloading `txt_download`. The follow cursor stays below a gap so
  an event AWX saves late is still printed. One whole-log download remains for
  `jobs logs` without `--follow` and for log expectations.
- Host summaries are one paginated read per job, only when the output format
  shows them (json, yaml, pipe), capped at 500 hosts with `hosts_truncated`;
  later expectations reuse them.
- An unpushed `--scm-branch HEAD` stays a `git` `config` error (exit 4), as
  the core error model shipped it: it is found locally before any launch.

The flat result fields are removed without aliases, with untaped 9.0.
