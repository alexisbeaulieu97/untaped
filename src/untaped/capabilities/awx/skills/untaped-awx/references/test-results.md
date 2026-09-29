# Test results (`awx.test_result`)

`untaped awx test run` prints one `awx.test_result` row per case, in the
order the cases are declared, then a summary on stderr
(`4 cases: 3 pass, 1 fail`). Read the rows with `--format json` (or `yaml`
or `pipe`); the table shows only `suite`, `case`, `result`, `job_status`,
`job_id`, `duration_s`, `failure.system` and `failure.summary`, and leaves out
the evidence and the host summaries.

## Exit code

A run exits with its most severe case (4 over 5 over 1), so read the exit
code first:

- 0: at least one case ran and every case passed.
- 1: a case failed because of the change or the suite: `awx.playbook`,
  `awx.scm`, `awx.expectation` or `awx.suite` (below). Also 1 when no case
  ran, or when the run stopped before launching because a suite, a vars file
  or a case is wrong (bad suite file, unknown template, a prompt not enabled):
  a preflight failure lists every failing case on stderr under `preflight
  failed, nothing launched:`.
- 2: a usage error (an unknown flag, a path that does not exist, no suite
  files found, a missing required `--var`).
- 4: the environment needs fixing, not your change: `awx.credentials` (a
  rejected token, a missing permission, a credential lookup that failed),
  `awx.inventory` (an inventory source failed to sync), or before any launch
  an error whose `system` is `git` (HEAD is not pushed or the checkout is not
  usable: push, or fix the checkout) or `local` (settings such as
  `awx.base_url` are missing).
- 5: temporary, retry later: `awx.controller` (AWX unavailable, a job that
  ended in `error` or never left `pending`) or `awx.hosts` (hosts
  unreachable).
- With `--format json` stderr is JSON Lines: an error that stops the run has
  `category`, `system`, `retryable`, `hint` and `exit_code`.
- 130: interrupted. Jobs still running are cancelled and listed on stderr
  (`interrupted: … cancel requested`); with `--no-cancel` they keep running
  and the list ends with an `untaped awx jobs wait …` command.

## Which system is responsible, and what to do

Every case that did not pass has a `failure`. Its `system` says who must act;
the first rule that matches decides:

| `system` | When | `category` (exit) | What to do |
|---|---|---|---|
| `awx.suite` | AWX refused the launch as the case asked it (an unknown name, a field the template ignores, a missing survey variable) | `invalid`, `not_found` (1) | Fix the suite or enable the prompt on the template, then `untaped awx test validate`. |
| `awx.credentials` | AWX rejected the token or a permission, or the job ended in `error` while looking up a credential | `auth`, `permission` (4) | Fix the token (`untaped awx ping` checks it) or the credential in AWX; do not change the playbook. |
| `awx.controller` | AWX was unreachable or failed while the run polled or read, the job ended in `error` (execution environment pull, capacity, a runner crash) or was canceled outside the run, or it never left `pending`/`waiting` before the timeout | `unavailable` (5) | Retry later; `evidence.job_explanation` and `evidence.result_traceback` say what AWX saw. |
| `awx.scm` | AWX names a failed `project_update` in the job's explanation (a branch not pushed, a bad ref, an SCM credential), or before launching `--scm-branch` on a template that does not prompt for it | `failed` (1), `invalid` before launching | Push the branch (`git push -u origin HEAD`) or fix the ref; read `evidence.related` and its log tail. |
| `awx.inventory` | AWX names a failed `inventory_update` in the job's explanation | `config` (4) | The inventory source is broken, not your change: read its log (`untaped awx jobs logs ID --kind inventory_update`). |
| `awx.hosts` | The job failed and every failed task is an unreachable host | `unavailable` (5) | Retry later, once the hosts in `evidence.unreachable_hosts` are reachable. |
| `awx.playbook` | The job failed any other way (a failed task, or no failed task at all: a syntax error or a missing role, shown in the log tail), or it was still running at the timeout (a hang) | `failed` (1) | Fix the playbook, role or variables: `evidence.failed_tasks` names the host, task and message. |
| `awx.expectation` | The job ran as intended but an expectation did not hold (it succeeded where the case expects a failure, or a log check failed) | `failed` (1) | Compare `expectations` with what the job did; fix the change or the case. |

Before blaming the change, confirm the job ran it: `scm_revision` must be
the commit you pushed.

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
| `scm_branch` | The ref the job ran, as AWX records it: `--scm-branch` or the case's `scm_branch` when given, otherwise the template's or project's branch. |
| `scm_revision` | The commit the job checked out; compare it with `git rev-parse HEAD` to be sure the job ran your change. |
| `failure` | Why the case did not pass (below); `null` for `pass`. |
| `expectations` | Every check, as `{check, expected, actual, passed}` (below). |
| `hosts` | Each host's PLAY RECAP counters by host name (below), in `json`, `yaml` and `pipe` output; `null` in a table, or when they could not be read. |
| `hosts_truncated` | `true` when the job ran on more than 500 hosts: `hosts` keeps the first 500 by name. |

### `failure`

| Field | Meaning |
|---|---|
| `system` | Who is responsible (the table above). |
| `category` | What kind of failure: it selects the exit code. |
| `retryable` | `true` only for `unavailable`: rerunning later may pass. |
| `summary` | One line: the task that failed and why, the update that failed first, the expectation that did not hold. |
| `hint` | What to do next, often an `untaped` command. |
| `evidence` | What shows it (below). |

### `evidence`

Filled in `json`, `yaml` and `pipe` output and with `--show-logs` (which also
prints the failure, failed tasks and log tail to stderr in any format). Each
field is `null` when it does not apply or could not be read.

| Field | Meaning |
|---|---|
| `job_explanation` | AWX's note on why the job ended, such as `Previous Task Failed: {"job_type": "project_update", …}`. |
| `result_traceback` | The end (last 2000 characters) of the controller's traceback of a job that ended in `error`. |
| `related` | The update the job depended on that failed first, as `{kind, id, name, status, url}`. |
| `log_tail` | The last 40 log lines of the responsible execution: `related` when set (the project update's log, not the empty job log), else the job. |
| `failed_tasks` | The responsible execution's failed tasks (below). |
| `unreachable_hosts` | The hosts among `failed_tasks` that could not be reached. |

`related` has these fields:

| Field | Meaning |
|---|---|
| `kind` | `project_update` or `inventory_update`. |
| `id` | Its id: `untaped awx jobs logs ID --kind KIND` prints its whole log. |
| `name` | The project or inventory source it updated. |
| `status` | Its status (`failed`). |
| `url` | Its output page in the controller web UI. |

`failed_tasks` has one entry per task that failed on a host, from the job's
events (`ignore_errors` failures are left out). It is `null` while AWX is
still saving the job's events (read them later with `untaped awx jobs events
ID`).

| Field | Meaning |
|---|---|
| `host`, `task` | Where it failed. |
| `status` | `failed`, or `unreachable` when the host could not be reached. |
| `msg` | The module's message (cut at 1000 characters). |
| `stderr` | The end of the module's stderr (its last 1000 characters), where command errors usually are. |

### `hosts`

One entry per host name, read once from the job's host summaries:

| Field | Meaning |
|---|---|
| `ok` | Tasks that succeeded without a change. |
| `changed` | Tasks that changed something. |
| `failed` | Tasks that failed. |
| `unreachable` | Tasks that could not reach the host (AWX's `dark`). |
| `skipped` | Tasks skipped by a condition. |
| `rescued` | Failures a `rescue` block handled. |
| `ignored` | Failures `ignore_errors` let pass. |

### `expectations`

One entry per check, in this order: `status`, then each `log.contains`,
`log.not_contains` and `log.matches` entry.

- `check`: `status`, `log.contains`, `log.not_contains` or `log.matches`.
- `expected`: the status, text or pattern the case asked for.
- `actual`: for `status`, the job's status; for a log check, the log line
  that decided it (the first line containing or matching the text, cut at
  300 characters), or `null` when no line did.
- `passed`: whether the check held.

For `awx.expectation`, `failure.summary` joins the failed checks, for example
`expected status failed, got successful; no log line contains 'PLAY RECAP'`.

## Verdicts

| `result` | Meaning |
|---|---|
| `pass` | The job reached a terminal status and every expectation held. |
| `fail` | The job finished but an expectation did not hold; `failure.system` says whether the playbook, a project or inventory update, the hosts, the controller or the expectation is responsible. |
| `error` | untaped could not launch, follow or read the job: AWX refused the launch or ignored fields (the case keeps any `job_id`), polling failed, or the log could not be downloaded for a log check. |
| `timeout` | The job was still unfinished at the case's timeout; it was cancelled (the summary says `cancel requested`) unless `--no-cancel`. `awx.controller` when it never started, `awx.playbook` when it hung while running: read `evidence.log_tail` for where. Raise the case's `timeout` only when the job is legitimately slow. |

## Useful follow-ups

```bash
untaped awx test run --case deploy-smoke/web --format json   # rerun one case
untaped awx test run --show-logs                             # evidence on stderr
untaped awx jobs logs 4410 --grep 'fatal:'                   # search a job's log
untaped awx jobs logs 812 --kind project_update              # a failed update's log
untaped awx jobs events 4410 --filter event=runner_on_failed # failed task events
```
