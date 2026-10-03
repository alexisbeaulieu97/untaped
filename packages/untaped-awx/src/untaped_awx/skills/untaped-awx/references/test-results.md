# Test results (`awx.test_result`)

`untaped awx test run` prints one `awx.test_result` row per case, in the
order the cases are declared, then a summary on stderr
(`4 cases: 3 pass, 1 fail`). Read the rows with `--format json` (or `yaml`
or `pipe`): the table leaves out the evidence and the host summaries.

- [Exit code](#exit-code)
- [Which system is responsible, and what to do](#which-system-is-responsible-and-what-to-do)
- The record's fields (`failure`, `evidence`, `hosts`, `nodes`, `expectations`): [test-result-fields.md](test-result-fields.md)
- [Idempotent cases](#idempotent-cases)
- [Workflow cases](#workflow-cases)
- [Comparing with a baseline](#comparing-with-a-baseline)
- Runs with temporary copies: [source-ref.md](source-ref.md#what-a-run-with-copies-reports)
- [Verdicts](#verdicts)
- [Useful follow-ups](#useful-follow-ups)

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
- 1: a case failed because of the change or the suite (`awx.playbook`,
  `awx.scm`, `awx.expectation` or `awx.suite`), or no case ran.
- 1 also when the preflight stopped the run: stderr lists every failing case
  under `preflight failed, nothing launched:`. With `--source-ref`, also
  when the specs or suites kept the copies from being provisioned.
- 2: a usage error (an unknown flag, a missing path, no suite files, a
  missing required `--var`).
- 4: fix the environment, not your change: `awx.credentials` (token,
  permission, credential lookup), `awx.inventory` (a failed source sync),
  `git` (HEAD not pushed, or an unusable checkout) or `local` (missing
  settings such as `awx.base_url`).
- 5: temporary, retry later: `awx.controller` or `awx.hosts`.
- 130: interrupted. Running jobs are cancelled and listed on stderr; with
  `--no-cancel` they keep running and the list ends with an
  `untaped awx jobs wait …` command.

With `--format json`, stderr is JSON Lines: an error that stops the run has
`category`, `system`, `retryable`, `hint` and `exit_code`. With a baseline,
a failure the baseline already had does not fail the run
([Comparing with a baseline](#comparing-with-a-baseline)).

## Which system is responsible, and what to do

Every case that did not pass has a `failure` whose `system` says who must
act. The first row that matches decides:

| `system` | When | `category` (exit) | What to do |
|---|---|---|---|
| `awx.suite` | AWX refused the launch as the case asked it: an unknown name, an ignored field, a missing survey variable | `invalid`, `not_found` (1) | Fix the suite or enable the prompt on the template, then `untaped awx test validate`. |
| `awx.credentials` | AWX rejected the token or a permission, or a credential lookup ended the job (or its update) in `error` | `auth`, `permission` (4) | Fix the token (`untaped awx ping`) or the credential in AWX; leave the playbook alone. |
| `awx.scm` | A `project_update` failed (see below) | `failed` (1); `invalid` before launching | Push the branch (`git push -u origin HEAD`) or fix the ref; read `evidence.related` and its log tail. |
| `awx.inventory` | AWX names a failed `inventory_update` in the job's explanation | `config` (4) | The inventory source is broken: read its log (`untaped awx jobs logs ID --kind inventory_update`). |
| `awx.controller` | AWX itself failed (see below) | usually `unavailable` (5); a job AWX no longer finds is `not_found` (1) | Retry later; `evidence.job_explanation` and `evidence.result_traceback` say what AWX saw. |
| `awx.expectation` | The job ran as intended but an expectation did not hold (see below) | `failed` (1) | Compare `expectations` with what the job did; fix the change or the case. |
| `awx.hosts` | The job failed and every failed task is an unreachable host | `unavailable` (5) | Retry once the hosts in `evidence.unreachable_hosts` are reachable. |
| `awx.playbook` | The job failed any other way, or was still running at the timeout (a hang) | `failed` (1) | Fix the playbook, role or variables: `evidence.failed_tasks` names the host, task and message. |

`awx.scm` covers:

- a failed `project_update` named in the job's explanation (a branch not
  pushed, a bad ref, an SCM credential), even when the case expected the job
  to fail;
- before launching, `--scm-branch` on a template that does not prompt for it.

`awx.controller` covers:

- AWX unreachable, or failing while the run polled or read;
- a job (or its update) that ended in `error`: an execution environment
  pull, capacity, a runner crash;
- a job canceled outside the run, or failed with a controller explanation
  (the job was lost), or failed before AWX saved its events;
- a check that needed the job's events while AWX was still saving them;
- a job that never left `pending`/`waiting` before the timeout.

`awx.expectation` covers:

- the job succeeded where the case expects a failure;
- a log check, a `changed` or `hosts` bound, or a `failed_tasks` entry
  failed;
- an `idempotent` rerun changed something;
- `failed_tasks not proven: …`: an entry matched only by a failure the host
  summaries cannot show was unhandled. A rerun reads the same recap, so
  instead of retrying, expect a task that fails unhandled, or check the log.

`awx.playbook` also covers a failure with no failed task at all (a syntax
error or a missing role, shown in the log tail).

A failure that is not AWX's keeps its own `system`: `untaped` for a bug in
untaped, `local` for local setup. Before blaming the change, confirm the job
ran it: `scm_revision` must be the commit you pushed.

## Idempotent cases

An `idempotent: true` case that passed is launched again (`rerun_job_id`).
Its row keeps the first job's fields and adds the `idempotent` check:

- The rerun succeeded and no host counts a changed task: `pass`.
- It succeeded but changed something: `fail`, `awx.expectation`,
  `not idempotent: the rerun ended successful, 2 changed`, with
  `evidence.changed_tasks` and the rerun's `log_tail`. Make those tasks
  report no change when nothing needs doing.
- It failed, errored or timed out: attributed like any job, with
  `failure.message` starting `rerun job 4412:` and evidence from the rerun.
- The check's `actual` is `unknown` when the rerun could not be followed,
  `not launched` when it could not be launched.

## Workflow cases

A workflow case's row has the same fields: `job_id`, `job_status` and
`rerun_job_id` are the workflow jobs', and `nodes` lists what each node ran.
`hosts` sums its node jobs' summaries per host, filled only when the case
bounds `changed` or `hosts`.

A failed workflow is blamed on the node that failed it: the first whose job
failed with no failure or always path out of it.

- That node's job is attributed by the rules above; `failure.message` is
  prefixed with the node (`node deploy: task 'Copy release' failed on web2:
  disk full`) and `evidence.node` names it.
- A nested workflow is followed the same way, 5 levels deep (`node
  release/verify: …`).
- A failing node check names its node too.
- A workflow that failed with no node to blame (for example a node whose
  template was deleted), or whose nodes could not be read, is
  `awx.controller`.
- A workflow still running at its timeout is blamed on the node still
  running: `awx.playbook` when it runs, `awx.controller` when it never
  started, `awx.suite` for an approval still waiting.

Approvals:

- A denied approval (`approvals: deny`) with no failure path out of it fails
  the workflow: `awx.expectation` unless the case expects `status: failed`.
- An approval denied or timed out outside the run: `awx.controller`.
- A pending approval the case does not answer: `error`, `awx.suite` (exit
  1). Set `approvals`. With `--no-cancel` the message gives the `untaped awx
  jobs cancel … --kind workflow_job` command.
- AWX refusing to approve (a missing Approve role, see
  [agent-profile.md](agent-profile.md)): `awx.credentials` (exit 4).
- A node that ran a project or inventory update itself and failed is
  `awx.scm` or `awx.inventory`, whatever the case expects.

## Comparing with a baseline

`--compare FILE` compares the run with the saved `--format json` or
`--format pipe` output of an earlier run. Save the base branch's results once
per task, outside the checkout so no commit picks the file up, then compare
every iteration:

```bash
untaped awx test run --scm-branch main --format json > /tmp/baseline-task.json
untaped awx test run --scm-branch HEAD --compare /tmp/baseline-task.json --format json
```

- The baseline run exits 1 when the base branch already fails some cases;
  those become `still_failing` rows.
- `--baseline REF` runs every selected case on `REF` first (as
  `--scm-branch REF`), then as asked, and compares. An `idempotent` case then
  launches 4 jobs.
- `--compare` and `--baseline` cannot be combined (exit 2). A missing file
  exits 2; a file that is not such output fails before any launch (exit 1).

Rows are matched by `suite` and `case`, and each gains `baseline` and
`change`:

| Field | Meaning |
|---|---|
| `result` | The case's verdict in the baseline. |
| `job_id` | The baseline's job. |
| `system` | The baseline's `failure.system`; `null` when it passed. |
| `category` | The baseline's `failure.category`; `null` likewise. |
| `node` | The baseline's `failure.evidence.node`; `null` for a job case. |

| `change` | Meaning | Fails the run |
|---|---|---|
| `regression` | It passed in the baseline and does not now, or it now fails because of another `system` (or in another workflow node). | yes |
| `unverified` | It fails now, and the baseline failed because of the environment (`auth`, `permission`, `config` or `unavailable`), so it proves nothing. | yes |
| `new` | The baseline did not run it; it counts as in a run without a baseline. | when it failed |
| `still_failing` | It fails now as in the baseline: same `system` and failing `node` (a baseline without a system matches on the result). | no, unless 4 or 5 |
| `fixed` | It did not pass in the baseline and passes now. | no |
| `pass` | It passed in both. | no |
| `removed` | The baseline ran it and this run did not (a case `--case` leaves out is not listed). Only `suite`, `case`, `baseline` and `change` are set. | no |

The exit code is then 0 when no case regressed and no new case failed, and 1
for a `regression`, an `unverified` row or a failing `new` case. A higher
code still wins: 4 or 5 for any case whose failure is the environment's or
temporary, in either run. The stderr summary adds a line counting each
change (`compared with the baseline: 1 regression, 1 fixed, 3 pass`).

## Verdicts

| `result` | Meaning |
|---|---|
| `pass` | The job reached a terminal status and every expectation held (and no update it waited for failed). |
| `fail` | The job finished but did not do what the case expects; `failure.system` says who is responsible. |
| `error` | untaped could not launch, follow or read the job: AWX refused the launch or ignored fields (the case keeps any `job_id`), polling failed, or a needed log could not be downloaded. |
| `timeout` | The job was unfinished at the case's timeout and was cancelled unless `--no-cancel`. Read `evidence.log_tail` for where it hung; raise `timeout` only when the job is legitimately slow. |

## Useful follow-ups

```bash
untaped awx test run --case deploy-smoke/web --format json   # rerun one case
untaped awx test run --show-logs                             # evidence on stderr
untaped awx jobs logs 4410 --grep 'fatal:'                   # search a job's log
untaped awx jobs logs 812 --kind project_update              # a failed update's log
untaped awx jobs events 4410 --filter event=runner_on_failed # failed task events
```
