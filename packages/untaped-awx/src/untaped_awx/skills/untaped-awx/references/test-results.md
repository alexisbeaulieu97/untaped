# Test results (`awx.test_result`)

`untaped awx test run` prints one `awx.test_result` row per case, in the
order the cases are declared, then a summary on stderr
(`4 cases: 3 pass, 1 fail`). Read the rows with `--format json` (or `yaml`
or `pipe`): the table leaves out the evidence and the host summaries.

- [Exit code](#exit-code)
- [Which system is responsible, and what to do](#which-system-is-responsible-and-what-to-do)
- [The record](#the-record): `failure`, `evidence`, `hosts`, `nodes`, `expectations`
- [Idempotent cases](#idempotent-cases)
- [Workflow cases](#workflow-cases)
- [Comparing with a baseline](#comparing-with-a-baseline)
- [Temporary copies](#temporary-copies)
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

## The record

The keys of a `--format json` row of the run you are already making list the
fields (`--columns '?'` would run every suite first, and needs a row to
inspect); the table shows the default ones. What the rest do not say:

- `result` is `null` only on a `removed` row ([Verdicts](#verdicts)).
- `duration_s` runs from launch to verdict.
- `job_status` is `null` when no job was read, and `job_id` when the launch
  failed before AWX created a job. `started_at` and `finished_at` are `null`
  while AWX has not set them.
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

## Temporary copies

With `--source-ref` ([test-suites.md](test-suites.md#temporary-test-sets---source-ref)),
the run creates its copies before any case launches. A case of a copied
template reports the copy's job, whose `scm_branch` is the commit. stderr
names each suite that runs a template AWX holds (`deploy: runs AWX's
JobTemplate 'Deploy' at 1a2b3c4 (no spec)`) and each copy created, with the
prompts it enables.

A copy that could not be provisioned is not a test result: the run stops
before any launch with one error and prints no row.

- The error starts `cannot provision the temporary test set of REF (nothing
  was created); nothing launched:` (or `(the copies created are torn
  down)`), or `cannot run REF; nothing launched:` when the run copies
  nothing.
- It lists each problem as `LABEL: message`, and carries the worst problem's
  `category`, `system` and `hint`.
- An invalid spec, or two specs matching one suite, fail alone as
  `awx.suite`, naming the files.

| `system` | When | `category` (exit) | What to do |
|---|---|---|---|
| `awx.suite` | A spec names something AWX does not have, a copy's name is taken, or two specs match one suite | `not_found`, `conflict`, `invalid` (1) | Fix the spec or suite (or create the missing resource in AWX), then `untaped awx test validate --source-ref REF`. |
| `awx.scm` | A copy's project does not allow branch override, or a template the run does not copy does not prompt for `scm_branch` | `invalid` (1) | Enable `allow_override` on the project; add the template's spec or enable `ask_scm_branch_on_launch`. |
| `awx.credentials` | AWX refused to create a copy (401, 403) | `auth`, `permission` (4) | Give the AWX user the roles in [agent-profile.md](agent-profile.md). |
| `awx.controller` | AWX was unavailable while creating a copy or reading a project | `unavailable` (5) | Retry later. |
| `awx` | AWX did not store a copy as the spec asks (`unverified …` fields) | `invalid` (1) | Rerun with `--keep` to inspect the copy, then fix the spec. |

After the results, each copy is deleted, workflows first, and named on
stderr; with `--keep` it is listed as `kept … (id N)`.

- A copy teardown could not delete in 30 seconds (a job of it still runs),
  or one a second Ctrl-C left, gets a `warning: teardown: … is left: …`
  line.
- The warning ends with the command that deletes that run's copies:
  `untaped awx test prune --run k3x9 --older-than 0`.
- The cases' results and the exit code stand.

`test validate --source-ref REF` (and `test run --source-ref REF --dry-run`)
prints one `awx.provision_outcome` row per copy it would create (`planned`);
`test prune` prints one `awx.prune_outcome` row per leftover copy (`planned`
with `--dry-run`, then `deleted` or `failed`). Both print the fields
of a `--format json` row (`--columns '?'` lists them under `--dry-run`).
`id` is `null` for a planned copy; `path` (`REF:PATH`
of its spec) and `prompts` are set on planned copies only. `created_at` is
when the run started, not when the copy was created. A copy is named
`NAME [untaped-test SHA RUN]`. A `failed` row's `error` carries the
attributed failure (`category`, `system`, `retryable`, `message`, `hint`).

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
