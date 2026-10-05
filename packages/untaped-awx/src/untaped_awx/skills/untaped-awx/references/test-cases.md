# Test cases

What one case of a suite holds: its launch payload, resource references,
expectations and timeouts. The suite around it is in
[test-suites.md](test-suites.md).

`untaped awx schema AwxTestSuite` lists a case's fields.

## `launch`: the launch payload

`launch` holds the fields of AWX's job template launch request:

- `extra_vars` (a mapping), `limit`, `inventory`, `credentials` (a list),
  `scm_branch`, `job_tags`, `skip_tags`, `job_type` (`run` or `check`),
  `verbosity` (0-4), `diff_mode`;
- `execution_environment`, `labels`, `instance_groups`, `forks`,
  `job_slice_count`, `timeout` (AWX's job timeout, not the case's wait) and
  `credential_passwords`.

A field outside this list is sent anyway with a warning (`unknown launch
field 'extra_var' — typo?`).

`inventory`, `credentials`, `execution_environment`, `labels` and
`instance_groups` take names:

- an inventory, credential or label name is looked up in the suite's
  `organization`, else `awx.default_organization`; the others are global;
- an integer is used as an AWX id;
- a single name where a list is expected is a one-item list.

Merging with `defaults.launch`, key by key:

- `extra_vars` is deep-merged: nested mappings merge, and the case wins for
  any other value.
- `credentials`, `labels` and `instance_groups` are concatenated, defaults
  first, without duplicates.
- Any other field in the case replaces the default's.
- `untaped awx test run --scm-branch REF` replaces `scm_branch` in every case.

## `!ref`: a resource by name

`!ref {kind: KIND, name: NAME}` is replaced by that resource's id, anywhere in
the body, including inside `extra_vars`:

```yaml
launch:
  inventory: !ref {kind: Inventory, name: "{{ env }} inventory"}
  extra_vars:
    target_project_id: !ref {kind: Project, name: Playbooks, organization: Ops}
```

- `kind` is an untaped kind name (`Inventory`, `Credential`, `Project`,
  `JobTemplate`, `WorkflowJobTemplate`, `ExecutionEnvironment`, `Label`,
  `InstanceGroup`, `Organization`, …).
- Other keys are the lookup scope and win; without them an
  organization-scoped kind is looked up as `launch` names are.
- Plain mappings are never treated as references.

## `expect`: what the job must produce

`untaped awx schema AwxTestSuite` lists the checks.

Every check must hold. Against `defaults.expect`:

- a case's `status`, `changed`, `idempotent` and `failed_tasks` replace the
  default's;
- each of its `log` lists replaces the same list;
- each counter a `hosts` entry sets replaces the default's for that host;
- whatever the case leaves out is inherited.

`status: failed` tests an intended failure: add `failed_tasks` naming the
task and message that prove why (see `negative.yml`). `validate` warns about
a `status: failed` case without them.

### `hosts`

A `hosts` entry sets upper bounds on one host's PLAY RECAP counters; a
counter it leaves out is not checked:

```yaml
expect:
  changed: 0
  hosts:
    "*": {failed: 0, unreachable: 0}
    web1: {changed: 0}
```

- **Merge per counter.** `defaults` and the case merge host by host and
  counter by counter, the case winning: a case's `"*": {changed: 5}` keeps
  the defaults' `"*": {failed: 0}`.
- **The named host wins.** A host's bound for a counter is its own entry's
  when set, else `"*"`'s: `web1: {changed: 3}` loosens `"*": {changed: 0}`
  for `web1` only.
- Bounds are numbers (`{changed: ">0"}` is invalid).
- A named host must be in the job's host summaries, so a misspelt host fails
  its check.
- A job on more than 500 hosts keeps 500 in the result, but the checks read
  every host over a bound, so no host past the cut can hide a failure.

Log, `changed`, `hosts` and `failed_tasks` checks read what AWX writes from
the job's events, so they wait until AWX has saved them. A job AWX is still
saving is an `awx.controller` error (exit 5, retry later), never a pass.

### `failed_tasks`

An entry matches a failed task, as the result's
`failure.evidence.failed_tasks` lists them, by `task`, `msg` or `matches`:

- An entry needs at least one part, and every part must match the same task.
- Every entry must match some failed task; other failed tasks do not fail
  the check.
- `ignore_errors` failures and failures a `rescue` block handled do not
  count.
- An entry matched only by a failure the host summaries cannot show was
  unhandled is not proven: an `awx.expectation` error (exit 1).
- A job that succeeded has no failed task, so its entries fail.

### `idempotent`

`idempotent: true` proves a second run changes nothing:

- Once the case passed every other check, the same resolved payload is
  launched again; the result adds `rerun_job_id`.
- When the payload sets `scm_branch` (or `--scm-branch` does), the rerun runs
  the commit the first job ran, so a push in between cannot change the
  comparison.
- The rerun must end `successful` with no changed task on any host. It waits
  the case's timeout and is cancelled like the first job.
- A case that did not pass is not rerun, nor is any case once the run is
  interrupted.
- An idempotent case must expect `status: successful`.

[test-results.md](test-results.md#idempotent-cases) says how a rerun fails.

## Timeouts and parallelism

- A case waits `--timeout` when given, else its own `timeout`, else
  `defaults.timeout`, else `awx.test_timeout_seconds` (1800 seconds).
- A job still running then, or when polling fails or Ctrl-C stops the run, is
  cancelled; `--no-cancel` leaves it running.
- `--parallel N` (default `awx.test_parallel`, 4) runs that many cases at
  once.
