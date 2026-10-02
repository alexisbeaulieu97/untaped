# Test suites (`AwxTestSuite`)

A test suite launches one job template (or one workflow) once per case, each
with its own launch payload, and checks each job against what the case
expects. This is the complete file format. `untaped awx schema AwxTestSuite`
prints the body's JSON Schema for editors and validators, and the
[examples](../examples/) are working starting points.

- [Where suites live](#where-suites-live)
- [File layout: header and body](#file-layout-header-and-body)
- [Header: `variables`](#header-variables)
- [Body: the suite](#body-the-suite)
- [Case body](#case-body): `launch`, `!ref`, `expect`, timeouts
- [Workflow suites](#workflow-suites)
- [Temporary test sets: `--source-ref`](#temporary-test-sets---source-ref)
- [Preflight: what `validate` and `run` check](#preflight-what-validate-and-run-check)
- [Commands](#commands)

## Where suites live

- Suites live in the repository they test, under `.untaped/awx/tests/` at the
  root of the git checkout; `untaped awx test init` writes one there.
- Without paths, `test run`, `test list` and `test validate` read every suite
  under that directory (the current directory outside a git checkout;
  without `git` on `PATH`, pass the paths).
- A directory is searched recursively: every `*.yml`/`*.yaml` file with a
  `kind: AwxTestSuite` key is a suite.
- Hidden files and directories are skipped and directory symlinks are not
  followed, so vars files and fixtures can sit beside the suites.
- A file named directly must be a suite. Suite names must be unique across
  the files read.

## File layout: header and body

A suite file is YAML. It may open with a header: a YAML mapping between two
`---` lines, before which only blank lines and `#` comments may come. The
rest, the body, is a Jinja2 template rendered with the header's variables and
then parsed as YAML:

```yaml
---
variables:
  env: {type: choice, choices: [staging, prod], default: staging}
---
kind: AwxTestSuite
name: deploy-smoke
jobTemplate: Deploy app
cases:
  web:
    launch:
      limit: "web-{{ env }}"
```

A lone `---` with no closing `---` is an ordinary YAML document-start marker,
not a header. `variables:` in the body is an error. Every error while reading
a suite names its file.

### Rendering rules

- An undefined variable is an error, never an empty string.
- Every Jinja2 construct works; loops build case matrices (see
  `variants.yml`).
- `{{ value | to_json }}` writes JSON (valid YAML, strings quoted) and
  `{{ value | to_yaml }}` a one-line YAML value. Use them for strings that
  might contain `:` or `#`, and for lists and mappings.
- Escape text meant for AWX that looks like Jinja2: `{% raw %}{{ ansible_host
  }}{% endraw %}` or `{{ '{{' }} ansible_host }}`.
- The rendered body must be a YAML mapping. A key repeated in one mapping
  (two cases a loop gave the same name) is an error, not an overwrite.
- The `!ref` tag works in the body (see [`!ref`](#ref-a-resource-by-name)).

## Header: `variables`

`variables` maps each variable's `name` to its declaration;
`untaped awx schema AwxTestSuite` lists the fields. Every field is optional,
and a variable without a `default` is required.

Values come from, highest precedence first: `--var`, `--vars-file` (a later
file wins), the `default`, then an interactive prompt for a required variable.

- Without a terminal, or with `--non-interactive`, a missing required
  variable fails instead of prompting.
- A `--var` or vars-file key no suite being read declares is an error; one
  that only another suite declares is ignored.
- Suite variables fill the template only: the extra vars AWX receives are
  each case's `launch.extra_vars`.

A secret goes in a `secret` variable, supplied from a vars file kept out of
the repository, and reaches the job through `extra_vars`:

```yaml
---
variables:
  db_password: {secret: true, description: Database password}
---
kind: AwxTestSuite
jobTemplate: Deploy app
cases:
  migrate:
    launch:
      extra_vars:
        db_password: {{ db_password | to_json }}
```

```bash
untaped awx test run --vars-file ~/.secrets/deploy-test.yml --non-interactive
```

## Body: the suite

`untaped awx schema AwxTestSuite` lists the body's fields. A suite names
exactly one of `jobTemplate` and `workflowTemplate`, and `cases` needs at
least one. `variables` is not written
in the body: `untaped awx test list` reports the header's declarations under
that key.

Unknown keys are errors everywhere in the body, so a typo such as
`expected:` fails validation instead of being ignored.

## Case body

`untaped awx schema AwxTestSuite` lists a case's fields.

### `launch`: the launch payload

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

### `!ref`: a resource by name

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

### `expect`: what the job must produce

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

#### `hosts`

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

#### `failed_tasks`

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

#### `idempotent`

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

### Timeouts and parallelism

- A case waits `--timeout` when given, else its own `timeout`, else
  `defaults.timeout`, else `awx.test_timeout` (1800 seconds).
- A job still running then, or when polling fails or Ctrl-C stops the run, is
  cancelled; `--no-cancel` leaves it running.
- `--parallel N` (default `awx.test_parallel`, 4) runs that many cases at
  once.

## Workflow suites

`workflowTemplate: NAME` makes every case launch a workflow job template (see
`workflow.yml`). Cases are written as for a job template, except:

- `launch` takes only what a workflow's launch takes: `extra_vars`,
  `inventory`, `limit`, `scm_branch`, `labels`, `job_tags` and `skip_tags`,
  each only when the workflow prompts for it. Another field warns as unknown.
- `expect.status` is the workflow job's status. `changed` and `hosts` bound
  its node jobs' host summaries, summed per host; `failed_tasks` matches the
  failed tasks of every node job that failed.
- `expect.log` is refused (a workflow job has no log); check a node's log
  under `nodes`.
- `idempotent: true` relaunches the whole workflow; the rerun must succeed
  with no changed task in any node job. It runs the first run's commit only
  when all its jobs ran the same one.

Approvals:

- `approvals: approve` (or `deny`) answers every approval the workflow waits
  on, in nested workflows too, as soon as it is pending.
- Without `approvals`, a pending approval fails the case at once: the
  workflow job is cancelled (unless `--no-cancel`) and the case is an
  `error` of `awx.suite` (exit 1).
- `validate` warns about each case without `approvals` whose workflow, or a
  nested one, has approval nodes.
- Answering needs AWX's Approve role on the workflow
  ([agent-profile.md](agent-profile.md)).

`expect.nodes` checks each named node's job with a case's own checks:

- Each key is a node id (AWX's node `identifier`, the `id` of an exported
  node; `untaped awx test init NAME --workflow` lists them).
- A key the workflow does not have fails the preflight with the closest ids.
- A node of a nested workflow is not addressed: check the node that runs
  that workflow.
- An entry merges over `defaults.expect.nodes` as a case's `expect` does,
  except that `status: never_ran` replaces the default's entry whole.

An entry takes the checks of a case except `idempotent`; `status:
never_ran` is for a node the workflow did not run, and no other check goes
with it.

- A node that ran an approval or a management job has only a status: an
  approved approval is `successful`, a denied or timed-out one `failed`.
- A node that ran a nested workflow is checked as a workflow (no `log`).
  Nested workflows are followed 5 levels deep.
- A negative case whose cause is named in a node needs no `failed_tasks` of
  its own.

## Temporary test sets: `--source-ref`

`--scm-branch` runs a branch's playbooks with the templates AWX holds. When
the branch also changes a template or workflow, keep its spec in the
repository ([specs.md](specs.md)) and run `untaped awx test run --source-ref
REF`: each suite runs against a temporary copy of its template as REF
describes it.

1. REF (a branch, tag or commit; `HEAD` once pushed) is pinned to its
   commit, which a remote must have. Suites and specs are read at that
   commit, never from the working tree; messages name files `REF:PATH`.
2. A suite binds to a spec by name: a `kind: JobTemplate` (for
   `jobTemplate`) or `kind: WorkflowJobTemplate` document anywhere under
   `.untaped/awx/` with the template's name and organization.
   - A copied workflow's nodes that run a template with a spec run that
     template's copy.
   - A spec in another organization does not bind, with a warning.
   - stderr names each suite that runs a template AWX holds.
3. Each copy is created as `apply` would create the spec, except:
   - its name is `NAME [untaped-test SHA RUN]` (7-digit commit and a random
     run id), so concurrent runs never collide;
   - its description carries the marker `untaped-test run=RUN ref=REF sha=SHA
     created=TIME` that `prune` finds leftovers by;
   - its `scm_branch` is the commit, so a job template's project must allow
     branch override; a workflow passes it to nodes that prompt for it;
   - it prompts on launch for every field a case sets in `launch`;
   - it has no webhook settings.

   Everything else a spec names must already exist: only the copies are
   created.
4. Every template the run launches without copying it must prompt for
   `scm_branch`: a suite's template without a spec, and each template a
   workflow of the run runs without a copy. Otherwise it would run its own
   branch, so the run is refused before anything is created: add the
   template's spec under `.untaped/awx/` or enable
   `ask_scm_branch_on_launch`.
5. The copies are created without a confirmation, the cases run, and the
   copies are deleted however the run ends (running jobs cancelled first);
   `--keep` keeps them.

- A case's `launch.scm_branch` is replaced by the commit.
- `--source-ref` cannot be combined with `--scm-branch` or `--baseline`
  (compare with `--compare`).
- `--no-cancel` needs `--keep`: AWX cannot delete a template while its job
  runs.
- `validate --source-ref REF` (or `run --source-ref REF --dry-run`) does
  everything but the writes and prints the copies it would create. A case of
  a copied template is checked against the spec.
- The AWX user needs to create and delete templates
  ([agent-profile.md](agent-profile.md)).
  [test-results.md](test-results.md#temporary-copies) covers what the run
  reports.

### Leftover copies: `test prune`

`untaped awx test prune` deletes copies a killed run left behind: templates
named like a copy whose description carries the matching marker, created
more than `--older-than` ago.

- An age below the longest run deletes copies of runs still going, whose
  next launches then fail. Prune another run's copies only once it has ended;
  `--run RUN` limits it to one run, as the teardown warning's hint does.
- It lists the copies and asks once; preview with `--dry-run`.
- It prints one `awx.prune_outcome` row per copy.

## Preflight: what `validate` and `run` check

`validate` renders, parses and resolves every case and checks it against its
template without launching; `run` does the same first and launches nothing
when any case fails. A case fails preflight when:

- the file does not render or validate;
- the template, or a name in `launch` or `!ref`, is not found or is
  ambiguous;
- a required survey variable is missing from `launch.extra_vars`;
- a launch field breaks the prompt rules of
  [jobs.md](jobs.md#launch-templates); a value the template already has is a
  no-op, left out of the launch;
- a workflow case checks a node id the workflow does not have.

Enable the prompt on the template, or drop the field. A field the preflight
does not know that AWX still ignores fails its case as `error` at launch, and
the job AWX launched anyway is cancelled (unless `--no-cancel`).

## Commands

```bash
untaped awx test init "Deploy app"                # starter suite from the survey and prompts
untaped awx test init Release --workflow          # starter suite for a workflow
untaped awx test list --var env=prod              # the cases that would run
untaped awx test run --case deploy-smoke/web --case db --non-interactive
untaped awx test run suites/deploy.yml --var env=prod --parallel 2 --show-logs
untaped awx test prune --run k3x9 --older-than 0 --yes   # one ended run's copies
```

`init` writes a commented suite with one `smoke` case:

- required survey variables get their default, else their first choice,
  else `TODO`;
- a password with a stored default gets `$encrypted$`, which makes AWX use
  the stored value; one without gets `TODO` (supply it from a `secret`
  variable, never write it in the file);
- optional survey variables and the launch fields the template prompts for
  are listed as comments;
- with `--workflow`, a comment lists the node ids (approvals marked), and
  the `smoke` case carries a commented `nodes:` example and, when the
  workflow has approval nodes, a commented `approvals: approve` to
  uncomment.

`validate` prints `SUITE/CASE: problem` on stderr per failing case and exits
1, else reports `N cases validated`. A `--case` that matches nothing is an
error before any launch.

`--scm-branch HEAD` is the current branch as named on its upstream remote; it
is refused while HEAD is detached, has no upstream, or is not pushed.
