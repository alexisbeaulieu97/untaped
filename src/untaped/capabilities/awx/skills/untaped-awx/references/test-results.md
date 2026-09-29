# Test results (`awx.test_result`)

`untaped awx test run` prints one `awx.test_result` row per case, in the
order the cases are declared, then a summary on stderr
(`4 cases: 3 pass, 1 fail`). Read the rows with `--format json` (or `yaml`
or `pipe`); the table shows only `suite`, `case`, `result`, `job_status`,
`job_id`, `duration_s` and `failure_reason`, and drops the evidence.

## Exit code

- 0: at least one case ran and every case passed.
- 1: any case did not pass, no case ran, or the run stopped before launching
  (preflight failure, bad suite file, unpushed `--scm-branch HEAD`). A
  preflight failure lists every failing case on stderr under
  `preflight failed, nothing launched:`.
- 2: a usage error (an unknown flag, a path that does not exist, no suite
  files found).
- 130: interrupted. Jobs still running are cancelled and listed on stderr
  (`interrupted: … cancel requested`); with `--no-cancel` they keep running
  and the list ends with an `untaped awx jobs wait …` command.

## The record

| Field | Meaning |
|---|---|
| `suite`, `case` | Which case this is (`--case suite/case` reruns it). |
| `result` | The verdict: `pass`, `fail`, `error` or `timeout` (below). |
| `job_status` | AWX's final status of the job (`successful`, `failed`, `error`, `canceled`), or its last seen status. `null` when no job was read. |
| `job_id` | The job's id (`null` when the launch failed before AWX created one). |
| `job_url` | The job's output page in the controller web UI. |
| `duration_s` | Seconds from launch to verdict. |
| `started_at`, `finished_at` | The job's start and finish times as AWX reports them. |
| `scm_branch` | The ref the job ran (`--scm-branch` or the case's `scm_branch`). |
| `scm_revision` | The commit the job checked out; compare it with `git rev-parse HEAD` to be sure the job ran your change. |
| `failure_reason` | One line saying why the case did not pass; `null` for `pass`. |
| `expectations` | Every check, as `{check, expected, actual, passed}` (below). |
| `failed_tasks` | For a case that did not pass: `{host, task, status, msg, stderr}` per failed task (below). |
| `log_tail` | For a case that did not pass: the last 40 lines of the job's stdout. |

`failed_tasks` and `log_tail` appear in `json`, `yaml` and `pipe` output, or
with `--show-logs` (which also prints them to stderr in any format). Each is
`null` when it could not be read; `failed_tasks` is also `null` while AWX is
still saving the job's events (read them later with `untaped awx jobs events
ID`).

### `expectations`

One entry per check, in this order: `status`, then each `log.contains`,
`log.not_contains` and `log.matches` entry.

- `check`: `status`, `log.contains`, `log.not_contains` or `log.matches`.
- `expected`: the status, text or pattern the case asked for.
- `actual`: for `status`, the job's status; for a log check, the log line
  that decided it (the first line containing or matching the text, cut at
  300 characters), or `null` when no line did.
- `passed`: whether the check held.

`failure_reason` joins the failed checks, for example `expected status
successful, got failed; no log line contains 'PLAY RECAP'`.

### `failed_tasks`

One entry per task that failed on a host, from the job's events
(`ignore_errors` failures are left out):

- `host`, `task`: where it failed.
- `status`: `failed`, or `unreachable` when the host could not be reached.
- `msg`: the module's message (cut at 1000 characters).
- `stderr`: the end of the module's stderr (its last 1000 characters),
  where command errors usually are.

## Verdicts and what to do

| `result` | Meaning | What to do |
|---|---|---|
| `pass` | The job reached a terminal status and every expectation held. | Nothing. |
| `fail` | The job finished but an expectation did not hold. | Read `expectations` for what differed. When `job_status` is `failed` but the case expected success, `failed_tasks` names the host, task and message: fix the playbook, role or variables. When the status is right but a log check failed, compare `actual` with `expected`, and the log tail. |
| `error` | untaped could not launch, follow or read the job: AWX refused the launch or ignored fields (`failure_reason` names them; the case keeps any `job_id`), polling failed, or the log could not be downloaded for a log check. | Check `failure_reason`. A refused launch is a template or suite problem (prompts, names, permissions); a polling or download error is usually transient: rerun the case. |
| `timeout` | The job was still running at the case's timeout; it was cancelled (the reason says `cancel requested`) unless `--no-cancel`. | Read `log_tail` for where it hung. Raise the case's `timeout` only when the job is legitimately slow. |

Before blaming the change, confirm the job ran it: `scm_revision` must be
the commit you pushed. A project update that failed before the job started
leaves the job `failed` or `error` with no failed tasks; read the log tail,
then `untaped awx jobs list --kind project_update --limit 5` and
`untaped awx jobs logs ID --kind project_update`.

## Useful follow-ups

```bash
untaped awx test run --case deploy-smoke/web --format json   # rerun one case
untaped awx test run --show-logs                             # evidence on stderr
untaped awx jobs logs 4410 --grep 'fatal:'                   # search a job's log
untaped awx jobs events 4410 --filter event=runner_on_failed # failed task events
```
