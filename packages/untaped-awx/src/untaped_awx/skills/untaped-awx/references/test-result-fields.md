# Test result fields

The fields of an `awx.test_result` row. What a failing run means and what to
do next is in [test-results.md](test-results.md).

## The record

The keys of a `--format json` row of the run you are already making list the
fields (`--columns '?'` would run every suite first, and needs a row to
inspect); the table shows the default ones. What the rest do not say:

- `result` is `null` only on a `removed` row ([Verdicts](test-results.md#verdicts)).
- `duration_s` runs from launch to verdict.
- `job_status` is `null` when no job was read, and `job_id` when the launch
  failed before AWX created a job. On a `timeout` row `job_status` is the
  last status seen before untaped cancelled the job (`running`, say), not a
  final one, unless the job ended before the cancel.
- `job_url` is the job's page in the controller web UI; open it to inspect
  the job.
- `started_at` and `finished_at` are `null` while AWX has not set them.
- `scm_revision` is the commit the job checked out; compare it with
  `git rev-parse HEAD`.
- `hosts` is `null` when the summaries were not or could not be read.
  `hosts_truncated` is `true` when the job ran on more than 500 hosts:
  `hosts` keeps 500, failed and unreachable hosts first.
- `nodes` is `null` for a job case, or when the nodes could not be read.
- `baseline` and `change` are `null` without a baseline; `baseline` is also
  `null` for a `new` case.
- `rerun_job_id` is `null` when no rerun was launched.

### `failure`

The same shape as the `error` of any failed untaped row, plus `evidence`.

| Field | Meaning |
|---|---|
| `system` | Who is responsible (the table above). |
| `category` | What kind of failure: it selects the exit code. |
| `retryable` | `true` only for `unavailable`: rerunning later may pass. |
| `message` | One line: the task that failed and why, the update that failed first, or the expectation that did not hold. |
| `hint` | What to do next, often an `untaped` command. |
| `evidence` | What shows it (below). |

### `evidence`

Filled in `json`, `yaml` and `pipe` output; `--show-logs` also prints the
failure, failed tasks and log tail to stderr in any format. Each field is
`null` when it does not apply or could not be read.

| Field | Meaning |
|---|---|
| `job_explanation` | AWX's note on why the job ended, such as `Previous Task Failed: {"job_type": "project_update", …}`. |
| `result_traceback` | The last 2000 characters of the controller's traceback of a job that ended in `error`. |
| `related` | The update the job depended on that failed first, as `{kind, id, name, status, url}`. |
| `log_tail` | The last 40 log lines of the responsible execution: `related` when set, else the job. |
| `failed_tasks` | The responsible execution's failed tasks (below). |
| `unreachable_hosts` | The hosts among `failed_tasks` that could not be reached. |
| `changed_tasks` | The tasks an `idempotent` case's rerun changed, as `{host, task}` (the first 100); `null` otherwise. |
| `note` | A second problem that did not decide the failure, such as `log fetch failed: …`. |
| `node` | A workflow case's node the failure is from (`outer/inner` inside a nested workflow); the other fields are then that node's job's. |

`related` has these fields:

| Field | Meaning |
|---|---|
| `kind` | `project_update` or `inventory_update`. |
| `id` | Its id: `untaped awx jobs logs ID --kind KIND` prints its whole log. |
| `name` | The project or inventory source it updated. |
| `status` | Its status as AWX reports it (`failed`, or `error` when the controller failed it). |
| `url` | Its output page in the controller web UI. |

`failed_tasks` has one entry per task that failed on a host, from the
execution's events:

- `ignore_errors` failures and failures a `rescue` block handled are left
  out. A host that counts N failures failed on its last N failed tasks; the
  ones before were rescued (likewise for `unreachable`).
- When a host has no summary, or its counters do not account for its failed
  tasks, all of them are listed.
- It is `null` when the events could not be read or were still being saved;
  read them later with `untaped awx jobs events ID`.

| Field | Meaning |
|---|---|
| `host`, `task` | Where it failed. |
| `status` | `failed`, or `unreachable` when the host could not be reached. |
| `msg` | The module's message (cut at 1000 characters). |
| `stderr` | The last 1000 characters of the module's stderr, where command errors usually are. |

### `hosts`

One entry per host name, from the job's host summaries:

| Field | Meaning |
|---|---|
| `ok` | Tasks that succeeded without a change. |
| `changed` | Tasks that changed something. |
| `failed` | Tasks that failed. |
| `unreachable` | Tasks that could not reach the host (AWX's `dark`). |
| `skipped` | Tasks skipped by a condition. |
| `rescued` | Failures a `rescue` block handled. |
| `ignored` | Failures `ignore_errors` let pass. |

### `nodes`

One entry per node of the workflow, in AWX's order:

| Field | Meaning |
|---|---|
| `id` | The node's id (AWX's node `identifier`), as `expect.nodes` names it. |
| `template` | What the node ran: the template's name, or the approval's. |
| `job_id` | The job (approval, nested workflow job, update) the node started; `null` when it never ran. |
| `status` | That job's status, or `never_ran`. |

### `expectations`

One entry per check, in this order: `status`, each `log.contains`,
`log.not_contains` and `log.matches` entry, `changed`, each `hosts` bound,
each node's checks for a workflow case (with a `node` key), each
`failed_tasks` entry, and last `idempotent`.

- `check`: `status`, `log.contains`, `log.not_contains`, `log.matches`,
  `changed`, `hosts`, `failed_tasks` or `idempotent`.
- `expected`: what the case asked for, such as `<= 0` for `changed`,
  `*: failed <= 0` for a `hosts` bound, or `successful, 0 changed` for
  `idempotent`.
- `actual` for a log check: the first line that decided it (cut at 300
  characters), or `null` when none did.
- `actual` for `hosts`: a named host's count (`null` when it is not in the
  summaries), or for `*` every host over the bound as `name=count`.
- `actual` for `failed_tasks`: the first failed task it matched, as
  `[host] task: msg`; for `status` and `changed`, the job's value.
- `passed`: whether the check held.

For `awx.expectation`, `failure.message` joins the failed checks, such as
`expected <= 0 changed tasks, got 3; no log line contains 'PLAY RECAP'`.

A check whose data could not be read makes the case an `error` when nothing
else failed, and a `note` otherwise. AWX saves a job's events after the job
ends; a check beyond `status` waits briefly for them. A job still being saved
after that is never checked on partial data: the case is an `awx.controller`
`error` (exit 5), `AWX is still saving the events of job 4412, …: only its
status was checked`. Retry later.
