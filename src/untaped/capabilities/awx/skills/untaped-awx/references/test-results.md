# Test results (`awx.test_result`)

`untaped awx test run` prints one `awx.test_result` row per case, in the
order the cases are declared, then a summary on stderr
(`4 cases: 3 pass, 1 fail`). Read the rows with `--format json` (or `yaml`
or `pipe`); the table shows only `suite`, `case`, `result` (then `change`
when comparing with a baseline), `job_status`, `job_id`, `duration_s`,
`failure.system` and `failure.message`, and leaves out the evidence and the
host summaries.

A failing row, abridged:

```json
{
  "suite": "deploy", "case": "prod-like", "result": "fail",
  "job_status": "error", "job_id": 4410,
  "failure": {
    "system": "awx.scm", "category": "failed", "retryable": false,
    "message": "project update 812 for 'acme-playbooks' failed: couldn't find remote ref feature/x",
    "hint": "push the branch, …; read its log: `untaped awx jobs logs 812 --kind project_update`",
    "evidence": {
      "related": {"kind": "project_update", "id": 812, "name": "acme-playbooks", "status": "failed", "url": "…"},
      "log_tail": ["fatal: [localhost]: FAILED! => {\"msg\": \"couldn't find remote ref feature/x\"}"]
    }
  },
  "hosts": {}
}
```

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
  `awx.inventory` (an inventory source failed to sync), or an error whose
  `system` is `git` (HEAD is not pushed or the checkout is not usable: push,
  or fix the checkout) or `local` (settings such as `awx.base_url` are
  missing).
- 5: temporary, retry later: `awx.controller` (AWX unavailable, a job that
  ended in `error` or never left `pending`) or `awx.hosts` (hosts
  unreachable).
- 130: interrupted. Jobs still running are cancelled and listed on stderr
  (`interrupted: … cancel requested`); with `--no-cancel` they keep running
  and the list ends with an `untaped awx jobs wait …` command.

With `--format json` stderr is JSON Lines: an error that stops the run has
`category`, `system`, `retryable`, `hint` and `exit_code`.

Compared with a baseline (`--compare` or `--baseline`), only a
`regression` fails the run with 1; see
[Comparing with a baseline](#comparing-with-a-baseline).

## Which system is responsible, and what to do

Every case that did not pass has a `failure`. Its `system` says who must act;
the first rule that matches decides:

| `system` | When | `category` (exit) | What to do |
|---|---|---|---|
| `awx.suite` | AWX refused the launch as the case asked it (an unknown name, a field the template ignores, a missing survey variable) | `invalid`, `not_found` (1) | Fix the suite or enable the prompt on the template, then `untaped awx test validate`. |
| `awx.credentials` | AWX rejected the token or a permission, or the job (or the update it waited for) ended in `error` looking up a credential | `auth`, `permission` (4) | Fix the token (`untaped awx ping` checks it) or the credential in AWX; do not change the playbook. |
| `awx.scm` | AWX names a failed `project_update` in the job's explanation (a branch not pushed, a bad ref, an SCM credential), even when the case expected the job to fail; or before launching, `--scm-branch` on a template that does not prompt for it | `failed` (1), `invalid` before launching | Push the branch (`git push -u origin HEAD`) or fix the ref; read `evidence.related` and its log tail. |
| `awx.inventory` | AWX names a failed `inventory_update` in the job's explanation | `config` (4) | The inventory source is broken, not your change: read its log (`untaped awx jobs logs ID --kind inventory_update`). |
| `awx.controller` | AWX was unreachable or failed while the run polled or read; the job (or its update) ended in `error` (execution environment pull, capacity, a runner crash); it was canceled outside the run, failed with a controller explanation (the job was lost), or failed before AWX saved its events; or it never left `pending`/`waiting` before the timeout | usually `unavailable` (5); a job AWX no longer finds while polling is `not_found` (1) | Retry later; `evidence.job_explanation` and `evidence.result_traceback` say what AWX saw. |
| `awx.expectation` | The job ran as intended but an expectation did not hold (it succeeded where the case expects a failure, a log check, a `changed` or `hosts` bound or a `failed_tasks` entry failed, or an `idempotent` rerun changed something) | `failed` (1) | Compare `expectations` with what the job did; fix the change or the case. |
| `awx.hosts` | The job failed and every failed task is an unreachable host | `unavailable` (5) | Retry later, once the hosts in `evidence.unreachable_hosts` are reachable. |
| `awx.playbook` | The job failed any other way (a failed task, or no failed task at all: a syntax error or a missing role, shown in the log tail), or it was still running at the timeout (a hang) | `failed` (1) | Fix the playbook, role or variables: `evidence.failed_tasks` names the host, task and message. |

A failure that is not AWX's keeps its own `system`: `untaped` for a bug in
untaped, `local` for local setup. Before blaming the change, confirm the job
ran it: `scm_revision` must be the commit you pushed.

## The record

| Field | Meaning |
|---|---|
| `suite`, `case` | Which case this is (`--case suite/case` reruns it). |
| `result` | The verdict: `pass`, `fail`, `error` or `timeout` (below); `null` only on a `removed` row. |
| `job_status` | AWX's final status of the job (`successful`, `failed`, `error`, `canceled`), or its last seen status. `null` when no job was read. |
| `job_id` | The job's id (`null` when the launch failed before AWX created one). |
| `rerun_job_id` | The job of an `idempotent` case's rerun; `null` when none was launched. |
| `job_url` | The job's output page in the controller web UI. |
| `duration_s` | Seconds from launch to verdict. |
| `started_at`, `finished_at` | The job's start and finish times as AWX reports them. |
| `scm_branch` | The ref the job ran, as AWX records it: `--scm-branch` or the case's `scm_branch` when given, otherwise the template's or project's branch. |
| `scm_revision` | The commit the job checked out; compare it with `git rev-parse HEAD` to be sure the job ran your change. |
| `failure` | Why the case did not pass (below); `null` for `pass`. |
| `expectations` | Every check, as `{check, expected, actual, passed}` (below). |
| `hosts` | Each host's PLAY RECAP counters by host name (below), in `json`, `yaml` and `pipe` output; `null` when they were not read or could not be. |
| `hosts_truncated` | `true` when the job ran on more than 500 hosts: `hosts` keeps 500, failed and unreachable hosts first. |
| `baseline` | The same case in the baseline run, when comparing (below); `null` otherwise, or for a `new` case. |
| `change` | How the case changed since the baseline (below); `null` without a baseline. |

### `failure`

The same shape as the `error` of any failed untaped row, plus `evidence`.

| Field | Meaning |
|---|---|
| `system` | Who is responsible (the table above). |
| `category` | What kind of failure: it selects the exit code. |
| `retryable` | `true` only for `unavailable`: rerunning later may pass. |
| `message` | One line: the task that failed and why, the update that failed first, the expectation that did not hold. |
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
| `changed_tasks` | The tasks an `idempotent` case's rerun changed, as `{host, task}` (the first 100); `null` otherwise. |
| `note` | A second problem that did not decide the failure, such as `log fetch failed: …` when the job's own failure is already known. |

`related` has these fields:

| Field | Meaning |
|---|---|
| `kind` | `project_update` or `inventory_update`. |
| `id` | Its id: `untaped awx jobs logs ID --kind KIND` prints its whole log. |
| `name` | The project or inventory source it updated. |
| `status` | Its status as AWX reports it (`failed`, or `error` when the controller failed it). |
| `url` | Its output page in the controller web UI. |

`failed_tasks` has one entry per task that failed on a host, from the
execution's events once AWX has saved them (`ignore_errors` failures, and
failures a `rescue` block handled, are left out). It is `null` when the
events could not be read or were still being saved (read them later with
`untaped awx jobs events ID`).

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
`log.not_contains` and `log.matches` entry, `changed`, each `hosts` bound,
each `failed_tasks` entry, and last `idempotent`.

- `check`: `status`, `log.contains`, `log.not_contains`, `log.matches`,
  `changed`, `hosts`, `failed_tasks` or `idempotent`.
- `expected`: the status, text or pattern the case asked for; `<= 0` for
  `changed`; `HOST: COUNTER <= N` for a `hosts` bound (`*: failed <= 0`);
  the entry's parts for `failed_tasks` (`task 'Validate', msg 'must be
  set'`); `successful, 0 changed` for `idempotent`.
- `actual`: for `status`, the job's status; for a log check, the log line
  that decided it (the first line containing or matching the text, cut at
  300 characters), or `null` when no line did; for `changed`, the total; for
  a named host, its count (`null` when the host is not in the summaries); for
  `*`, every host over the bound as `name=count` (`null` when none is); for
  `failed_tasks`, the first failed task it matched as `[host] task: msg`
  (`null` when none did); for `idempotent`, the rerun's status and changed
  total (`not launched` when the rerun launch failed).
- `passed`: whether the check held.

For `awx.expectation`, `failure.message` joins the failed checks, for example
`expected status failed, got successful; no log line contains 'PLAY RECAP'`,
`expected <= 0 changed tasks, got 3`, `expected web1: changed <= 0, got 2`
or `no failed task matches task 'Validate', msg 'must be set'`. A check whose
data could not be read (the log, the host summaries, the job's events) makes
the case an `error` when nothing else failed, and a `note` otherwise.

## Idempotent cases

An `idempotent: true` case that passed is launched again with the same
payload (`rerun_job_id`). Its row keeps the first job's fields and adds the
`idempotent` check:

- The rerun succeeded and no host counts a changed task: `pass`.
- It succeeded but changed something: `fail`, `awx.expectation`,
  `not idempotent: rerun job 4412 changed 2 tasks`, with
  `evidence.changed_tasks` listing each host and task (read once from the
  rerun's `runner_on_ok` events with `changed=true`) and the rerun's
  `log_tail`. Make those tasks report no change when nothing needs doing.
- It failed, errored or timed out: the rerun is attributed like any job, its
  `failure.message` starts with `rerun job 4412:` and its evidence comes from
  the rerun. A rerun still running at the case's timeout is cancelled
  (unless `--no-cancel`).

## Comparing with a baseline

`--compare FILE` compares the run with the saved output of an earlier run:
`untaped awx test run --format json` (a list of rows) or `--format pipe`
(`awx.test_result` records). Save the base branch's results once per task,
then compare every iteration without running the base again:

```bash
untaped awx test run --scm-branch main --format json > baseline.json
untaped awx test run --scm-branch HEAD --compare baseline.json --format json
```

`--baseline REF` does both in one command: it runs every selected case on
`REF` first (as `--scm-branch REF`, so templates must prompt for it; `HEAD`
must be pushed), then as asked, and compares. `--compare` and `--baseline`
cannot be combined (exit 2). A file that is not such output fails before any
launch (exit 1, naming the file).

Rows are matched by `suite` and `case`. Each row gains `baseline` and
`change`:

| Field | Meaning |
|---|---|
| `result` | The case's verdict in the baseline. |
| `job_id` | The baseline's job. |
| `system` | The baseline's `failure.system`; `null` when it passed. |

| `change` | Meaning |
|---|---|
| `regression` | It passed in the baseline and does not now. |
| `fixed` | It did not pass in the baseline and passes now. |
| `still_failing` | It did not pass in either. |
| `pass` | It passed in both. |
| `new` | The baseline did not run it (`baseline` is `null`). |
| `removed` | The baseline ran it and this run did not. These rows follow the others and have no job: only `suite`, `case`, `baseline` and `change` are set (`result`, `job_id`, `failure` and the other job fields are `null`). A baseline case `--case` does not select is left out. |

The exit code then says whether the change broke anything: 0 when nothing
regressed, 1 only for a `regression`. A higher code still wins: 4 or 5 for
any case of this run whose failure is the environment's or temporary
(whatever its `change`; with `--baseline`, of the baseline run too, since the
comparison needs it), and 2 and 130 as always. A `still_failing` or `new`
case that fails for another reason does not fail the run: read its `result`.
The stderr summary adds a line counting each change
(`compared with the baseline: 1 regression, 1 fixed, 3 pass`).

## Verdicts

| `result` | Meaning |
|---|---|
| `pass` | The job reached a terminal status and every expectation held (and no update it waited for failed). |
| `fail` | The job finished but did not do what the case expects; `failure.system` says whether the playbook, a project or inventory update, the hosts, the controller or the expectation is responsible. |
| `error` | untaped could not launch, follow or read the job: AWX refused the launch or ignored fields (the case keeps any `job_id`), polling failed, or the log a log check needs could not be downloaded. |
| `timeout` | The job was still unfinished at the case's timeout; it was cancelled (the message says `cancel requested`) unless `--no-cancel`. `awx.controller` when it never started, `awx.playbook` when it hung while running: read `evidence.log_tail` for where. Raise the case's `timeout` only when the job is legitimately slow. |

## Useful follow-ups

```bash
untaped awx test run --case deploy-smoke/web --format json   # rerun one case
untaped awx test run --show-logs                             # evidence on stderr
untaped awx jobs logs 4410 --grep 'fatal:'                   # search a job's log
untaped awx jobs logs 812 --kind project_update              # a failed update's log
untaped awx jobs events 4410 --filter event=runner_on_failed # failed task events
```
