# Test suites (`AwxTestSuite`)

A test suite launches one job template (or one workflow, see
[Workflow suites](#workflow-suites)) several times, once per case, with a
different launch payload each time, and checks each job against what the case
expects. This is the complete file format; `untaped awx schema AwxTestSuite`
prints the body's JSON Schema for editors and validators, and the
[examples](../examples/) are working starting points.

## Where suites live

- Suites live in the repository they test, under `.untaped/awx/tests/` at the
  root of the git checkout. `untaped awx test init TEMPLATE` writes a starter
  suite there.
- Without paths, `untaped awx test run`, `untaped awx test list` and
  `untaped awx test validate` read every suite under that directory (the
  current directory outside a git checkout; without `git` on `PATH`, pass
  the paths).
- A directory path is searched recursively: every `*.yml`/`*.yaml` file with
  a `kind: AwxTestSuite` key (quoted or not, with a trailing `# comment` or
  not) is a suite. Hidden files and directories are
  skipped and directory symlinks are not followed, so vars files and fixtures
  can sit beside the suites. A file named directly must be a suite.
- Each file is read once, and suite names must be unique across the files
  read.

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

A file without a header is just the body. A lone `---` with no closing
`---` is an ordinary YAML document-start marker, not a header. `variables:`
in the body is an error: declare variables in the header. Every error while
reading a suite names its file.

### Rendering rules

- An undefined variable is an error (`undefined Jinja2 variable: …`), never
  an empty string.
- Every Jinja2 construct works: `{{ var }}`, `{% for %}`, `{% if %}`,
  filters. Loops build case matrices (see `variants.yml`).
- Two extra filters interpolate values safely: `{{ value | to_json }}`
  writes JSON (valid YAML, strings quoted), and `{{ value | to_yaml }}` writes
  a one-line YAML value. Use them for strings that might contain `:` or `#`,
  and for lists and mappings.
- Text meant for AWX that looks like Jinja2 (`{{ ansible_host }}` in
  `extra_vars`) must be escaped: `{% raw %}{{ ansible_host }}{% endraw %}` or
  `{{ '{{' }} ansible_host }}`.
- The rendered body must be a YAML mapping. A key repeated in one mapping
  (for example two cases a loop gave the same name) is an error, not a silent
  overwrite.
- The `!ref` tag works in the body (see [`!ref`](#ref-a-resource-by-name)).

## Header: `variables`

`variables` maps each variable name (its `name`) to its declaration. Every
field is optional:

| Field | Meaning |
|---|---|
| `type` | `string` (default), `int`, `bool`, `choice` or `list`; a supplied value is converted to it. `bool` accepts `true/false`, `yes/no`, `on/off`, `1/0`. `list` accepts a YAML list or a comma-separated string (`--var regions=us-east,eu-west`). |
| `description` | The prompt text when asked interactively (default: the name). |
| `default` | The value when neither `--var` nor `--vars-file` sets the variable. A variable without a default is required. |
| `choices` | The allowed values of a `choice` variable; required for that type, and a `default` must be one of them. |
| `secret` | `true` prompts without echoing the answer. |

Values come from, in order of precedence:

1. `--var KEY=VALUE` (repeatable);
2. `--vars-file FILE` (repeatable; a YAML or JSON mapping with string keys; a
   later file wins over an earlier one);
3. the variable's `default`;
4. an interactive prompt, for a required variable only.

Without a terminal, or with `--non-interactive`, a missing required variable
fails (`required variable not provided: env; set them with --var NAME=VALUE
…`) instead of prompting. A `--var` or vars-file key that no suite being read
declares is an error listing the declared names; one that only another suite
declares is accepted and ignored. Suite variables fill the template only:
the extra vars AWX receives are each case's `launch.extra_vars`.

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

| Field | Meaning |
|---|---|
| `kind` | Required, exactly `AwxTestSuite`. |
| `name` | The suite name used by `--case SUITE/CASE`; default: the file name without its extension. |
| `jobTemplate` | The name of the job template every case launches. A suite names exactly one of `jobTemplate` and `workflowTemplate`. |
| `workflowTemplate` | The name of the workflow job template every case launches, instead of `jobTemplate` (see [Workflow suites](#workflow-suites)). |
| `organization` | The template's organization when its name is not unique (default: `awx.default_organization`). |
| `defaults` | A case body every case inherits (`launch`, `expect`, `timeout`, `approvals`). |
| `cases` | Required, at least one. Case name → case body; each case launches the template once. |
| `variables` | Not written in the body: `untaped awx test list` reports the header's declarations under this key. |

Unknown keys are errors everywhere in the body, so a typo such as
`expected:` or `jobtemplate:` fails validation instead of being ignored.

## Case body

| Field | Meaning |
|---|---|
| `launch` | The AWX launch payload for this case (see below). |
| `expect` | What the job must produce (see below). |
| `timeout` | Seconds (a positive number) to wait for the job; then it is cancelled and the case is `timeout`. |
| `approvals` | A workflow case only: `approve` or `deny` every approval the workflow waits on (see [Workflow suites](#workflow-suites)); a case's replaces `defaults.approvals`. |

### `launch`: the launch payload

`launch` holds the fields of AWX's job template launch request:

- `extra_vars` (a mapping), `limit`, `inventory`, `credentials` (a list),
  `scm_branch`, `job_tags`, `skip_tags`, `job_type` (`run` or `check`),
  `verbosity` (0-4), `diff_mode`;
- `execution_environment`, `labels`, `instance_groups`, `forks`,
  `job_slice_count`, `timeout` (AWX's job timeout, not the case's wait) and
  `credential_passwords`.

A field outside this list is sent anyway with a warning
(`unknown launch field 'extra_var' — typo?`).

`inventory`, `credentials`, `execution_environment`, `labels` and
`instance_groups` take names. A name of an organization-scoped kind
(inventory, credential, label) is looked up in the suite's `organization`,
else `awx.default_organization`; the others are global. An integer is used as
an AWX id as it is. A single name where a list is expected
is treated as a one-item list.

**Merging with `defaults.launch`**, key by key:

- `extra_vars` is deep-merged: nested mappings merge, and the case wins for
  any other value.
- `credentials`, `labels` and `instance_groups` are concatenated, defaults
  first, without duplicates.
- Any other field in the case replaces the default's.

`untaped awx test run --scm-branch REF` replaces `scm_branch` in every case.

### `!ref`: a resource by name

`!ref {kind: KIND, name: NAME}` is replaced by that resource's id. It works
anywhere in the body, including inside `extra_vars`, so a playbook can
receive an id it needs:

```yaml
launch:
  inventory: !ref {kind: Inventory, name: "{{ env }} inventory"}
  extra_vars:
    target_project_id: !ref {kind: Project, name: Playbooks, organization: Ops}
```

`kind` is an untaped kind name (`Inventory`, `Credential`, `Project`,
`JobTemplate`, `WorkflowJobTemplate`, `ExecutionEnvironment`, `Label`,
`InstanceGroup`, `Organization`, …). Any other keys are the lookup scope
(`organization: Ops`) and win; without them an organization-scoped kind is
looked up in the suite's `organization`, else `awx.default_organization`.
Plain mappings are never treated as references.

### `expect`: what the job must produce

| Field | Meaning |
|---|---|
| `status` | The job's final status: `successful` (the default), `failed`, `error` or `canceled`. |
| `log` | Checks on the job's full stdout, line by line. |
| `log`: `contains` | Texts that some line must contain. |
| `log`: `not_contains` | Texts that no line may contain. |
| `log`: `matches` | Python regular expressions that some line must match (searched anywhere in the line; an invalid pattern fails validation). |
| `changed` | The most tasks that may change something, summed over every host's `changed` counter in the job's host summaries; `0` means the job changes nothing. |
| `hosts` | Upper bounds on each host's counters, by host name (below); `"*"` bounds every host. |
| `idempotent` | `true`: once the case passed, launch it again with the same payload; the rerun must succeed and change nothing (below). |
| `failed_tasks` | Failed tasks the job must have (below): proves a negative case failed for the right reason. |
| `nodes` | A workflow case only: checks on each node's job, by node id (see [Workflow suites](#workflow-suites)). |

Every check must hold. A case's `status`, `changed`, `idempotent` and
`failed_tasks` replace the default's, each of its `log` lists replaces the
same list in `defaults.expect`, and each counter a `hosts` entry sets
replaces the default's for that host (below); whatever the case leaves out
is inherited. `status: failed` tests an intended failure: add `failed_tasks`
naming the task and message that prove the job failed for the right reason
(see `negative.yml`); `untaped awx test validate` warns about a case that
expects `status: failed` without `failed_tasks`.

A `hosts` entry sets upper bounds on one host's PLAY RECAP counters; a
counter it leaves out is not checked:

| Field | Meaning |
|---|---|
| `failed` | The most tasks that may fail on the host. |
| `unreachable` | The most tasks that may find the host unreachable. |
| `changed` | The most tasks that may change the host. |

```yaml
expect:
  changed: 0
  hosts:
    "*": {failed: 0, unreachable: 0}
    web1: {changed: 0}
```

Two rules decide each host's bounds:

- **Merge per counter.** `defaults` and the case merge host by host and
  counter by counter, the case winning: a case's `"*": {changed: 5}` keeps
  the defaults' `"*": {failed: 0}`.
- **The named host wins.** A host's bound for a counter is its own entry's
  when that entry sets the counter, else `"*"`'s. So `web1: {changed: 3}`
  loosens `"*": {changed: 0}` for `web1` only, and `web1` still gets
  `"*"`'s `failed` bound.

Bounds are numbers (`{changed: ">0"}` is invalid). A named host must be in
the job's host summaries, so a misspelt host fails its check. `changed` and
`hosts` read the job's host summaries in every output format. A job on more
than 500 hosts keeps 500 in the result (failed and unreachable hosts first);
the checks then read the hosts over each bound, and the named hosts, with
filtered reads, so no host past the cut can hide a failure. Log, `changed`,
`hosts` and `failed_tasks` checks read what AWX writes from the job's
events, so they wait until AWX has saved them; a job AWX is still saving is
an `awx.controller` error (exit 5, retry later), never a pass.

A `failed_tasks` entry matches a failed task: a task that failed on a host or
found it unreachable, as the result's `failure.evidence.failed_tasks` lists
them (`ignore_errors` failures and failures a `rescue` block handled do not
count: a host that counts N failures failed on its last N failed tasks). It
needs at least one part, and every part it gives must match the same task.
Every entry must match some failed task; other failed tasks do not fail the
check. An entry that only a failure the host summaries cannot show was
unhandled matches is not proven: the case is an `awx.expectation` error
(exit 1; a rerun reads the same recap). A
job that succeeded has no failed task, so its `failed_tasks` entries fail
without reading anything.

| Field | Meaning |
|---|---|
| `task` | Text the task's name must contain. |
| `msg` | Text the task's message (the module's `msg`) must contain. |
| `matches` | A Python regular expression the task's message must match (searched anywhere in it). |

`idempotent: true` proves a second run changes nothing. Once the case passed
every other check, the same resolved payload is launched again; the result
keeps `job_id` and adds `rerun_job_id`. When the payload sets `scm_branch`
(or `--scm-branch` does), the rerun launches on the commit the first job ran
(its `scm_revision`), so a push in between cannot change what is compared.
The rerun must end `successful` with no changed task on any host. It waits
the case's timeout and is cancelled like the first job. A case that did not
pass is not rerun, nor is any case once the run is interrupted. An
idempotent case must expect `status: successful` (the default); another
status is refused when the suite is read. See
[test-results.md](test-results.md#idempotent-cases) for how a rerun fails.

### Timeouts and parallelism

A case waits `--timeout SECONDS` when given, else its own `timeout`, else
`defaults.timeout`, else `awx.test_timeout` (1800 seconds). A job still
running then is cancelled and the case is `timeout`; `--no-cancel` leaves it
running. `--parallel N` (default `awx.test_parallel`, 4) runs that many cases
at once. A polling error or Ctrl-C cancels the job too (unless
`--no-cancel`).

## Workflow suites

`workflowTemplate: NAME` instead of `jobTemplate` makes every case launch a
workflow job template (see `workflow.yml`). Cases are written as for a job
template, with these differences:

- `launch` takes the fields a workflow's launch takes: `extra_vars`,
  `inventory`, `limit`, `scm_branch`, `labels`, `job_tags` and `skip_tags`,
  each only when the workflow prompts for it (`extra_vars` also through its
  survey). Another field warns as unknown. The workflow passes them to its
  nodes as AWX does.
- `approvals: approve` (or `deny`) answers every approval the workflow waits
  on, in nested workflows too, as soon as it is pending. Without `approvals`
  a pending approval fails the case at once: the workflow job is cancelled
  (with `--no-cancel` it keeps running, and the message says how to finish
  it) and the case is an `error` of `awx.suite` (exit 1). `untaped awx test
  validate` warns about each case without `approvals` whose workflow, or a
  workflow nested in it, has approval nodes. Answering needs AWX's Approve
  role on the workflow (see [agent-profile.md](agent-profile.md)). A
  workflow without approval nodes is polled for its status only.
- `expect.status` is the workflow job's status; `changed` and `hosts` bound
  its node jobs' host summaries, summed per host; `failed_tasks` matches the
  failed tasks of every node job that failed. `expect.log` is refused (a
  workflow job has no log): check a node's log under `nodes`.
- `idempotent: true` launches the whole workflow again; its rerun must
  succeed with no changed task in any node job. When the payload sets
  `scm_branch`, the rerun launches on the commit the first run's jobs ran,
  if they all ran the same one.
- `expect.nodes` checks each named node's job with a case's own checks.
  Each key is a node id of the workflow (AWX's node `identifier`, the `id`
  of an exported node; `untaped awx test init NAME --workflow` lists them),
  and a key the workflow does not have fails the preflight with the closest
  ids. A node of a nested workflow is not addressed: check the node that
  runs that workflow.

A `nodes` entry takes these checks, which merge over the same node's entry
in `defaults.expect.nodes` as a case's `expect` does, except that a case's
`status: never_ran` replaces the default's entry for that node whole:

| Field | Meaning |
|---|---|
| `status` | The node's final status: `successful` (the default), `failed`, `error`, `canceled`, or `never_ran` for a node the workflow did not run (no other check goes with it). |
| `log` | Checks on the node job's stdout (`contains`, `not_contains`, `matches`). |
| `changed` | The most tasks the node's job may change, over all its hosts. |
| `hosts` | Upper bounds on each host's counters in the node's job, as for a case. |
| `failed_tasks` | Failed tasks the node's job must have. |

A node that ran an approval (or a management job) has only a status: an
approved approval is `successful`, a denied or timed-out one `failed`. A
node that ran a nested workflow is checked as a workflow (no `log`).
Nested workflows are followed 5 levels deep, for approvals and for blame.
[`workflow.yml`](../examples/workflow.yml) shows each of these.

A negative case (`status: failed`) that names its cause in a node (a node
that must fail, or its `failed_tasks`) needs no `failed_tasks` of its own:
`validate` does not warn about it.

## Temporary test sets: `--source-ref`

`--scm-branch` runs a branch's playbooks with the templates AWX holds. When
the branch also changes a template or workflow, keep its spec in the
repository ([specs.md](specs.md), the document `export` writes) and run
`untaped awx test run --source-ref REF`: each suite then runs against a
temporary copy of its template as the ref describes it.

1. REF (a branch, tag or commit; `HEAD` once pushed) is pinned to its
   commit, which a remote must have: AWX checks it out. Suites and specs are
   read at that commit, never from the working tree (paths default to
   `.untaped/awx/tests/` at the repository root; messages name files
   `REF:PATH`).
2. A suite is bound by name: when a `kind: JobTemplate` (for `jobTemplate`)
   or `kind: WorkflowJobTemplate` (for `workflowTemplate`) document anywhere
   under `.untaped/awx/` has the template's name and organization (the
   suite's `organization`, else `awx.default_organization`), the suite runs a
   copy of it. A copied workflow's nodes that run a template with a spec
   (same name and organization) run that template's copy. A spec of the
   template in another organization does not bind, with a warning; each
   suite that runs a template AWX holds is named on stderr.
3. Each copy is created as `apply` would create the spec, with these
   changes:
   - its name is `NAME [untaped-test SHA RUN]` (the commit's first 7 digits
     and a random run id), so concurrent runs never collide; a name already
     taken is refused;
   - its description is the marker `untaped-test run=RUN ref=REF sha=SHA
     created=TIME` that `prune` finds leftovers by;
   - its `scm_branch` is the commit, so a job template's project must allow
     branch override (`allow_override`); a workflow passes it to its nodes
     that prompt for it;
   - it prompts on launch (`ask_*_on_launch`) for every field a case sets in
     `launch`, so a case can target a test inventory without changing the
     spec;
   - it has no webhook settings.

   Everything a spec names (project, inventory, credentials, execution
   environment, labels, instance groups, the templates its nodes run) is
   looked up by name and must exist: nothing but the copies is created.
4. Every template the run launches without copying it must prompt for
   `scm_branch`, which is then the commit: a suite's template without a
   spec, and each job template or workflow a workflow of the run (copied or
   not, nested ones too) runs without a copy. Any other would silently run
   its own branch, so the run is refused before anything is created, naming
   the suite and node: add its spec under `.untaped/awx/` or enable
   `ask_scm_branch_on_launch`.
5. The copies are created without a confirmation, the cases run, and the
   copies are deleted however the run ends (running jobs are cancelled
   first); `--keep` keeps them. See
   [test-results.md](test-results.md#temporary-copies) for what the run
   reports, a copy it could not provision or delete included.

A case's `launch.scm_branch` is replaced by the commit, as `--scm-branch`
does. `--source-ref` cannot be combined with `--scm-branch` or `--baseline`
(compare with `--compare`), and `--no-cancel` needs `--keep` (AWX cannot
delete a template while its job runs). The AWX user needs to create and
delete job templates and workflows ([agent-profile.md](agent-profile.md)).

`untaped awx test validate --source-ref REF` (or `untaped awx test run
--source-ref REF --dry-run`) does everything but the writes and prints one
`awx.provision_outcome` row per copy; a case of a copied template is checked
against the spec (its survey's required variables, the node ids it checks).

`untaped awx test prune` deletes the copies a killed run left behind: job
templates and workflows named like a copy whose description carries the
matching marker, created more than `--older-than` ago (`2h` by default;
`30m`, `1d`, `90s`; `0` takes every copy). `--run RUN` takes only one run's
copies, as the teardown warning's hint does. An age below the longest run
deletes the copies of runs still going, whose next launches then fail: prune
another run's copies only once it has ended. It lists them and asks once
(`--yes` skips the question, `--dry-run` only lists them), and prints one
`awx.prune_outcome` row per copy.

## Preflight: what `validate` and `run` check

`untaped awx test validate` renders, parses and resolves every case and
checks it against its job template without launching anything; `untaped awx
test run` does the same first and launches nothing when any case fails. A
case fails preflight when:

- the file does not render or validate (the message names the file);
- the job template (the error suggests close names), or a name in `launch`
  or `!ref`, is not found or is ambiguous;
- a required survey variable (one AWX lists in `variables_needed_to_start`)
  is missing from `launch.extra_vars`;
- the case sets `extra_vars`, `limit`, `inventory`, `credentials`,
  `scm_branch`, `job_tags`, `skip_tags`, `verbosity`, `diff_mode` or
  `job_type` while the template's matching `ask_*_on_launch` is false (AWX
  would ignore it), unless the template has that value already: its own,
  its project's branch for an `scm_branch` it does not set, or extra vars
  it saves with those values (AWX treats these as no-ops, and the run
  leaves them out of the launch);
- the template has a survey but does not prompt for variables, and
  `extra_vars` holds a variable outside the survey;
- a workflow case checks a node id the workflow does not have
  (`workflow node not found: 'deplyo' in workflow 'Release'; did you mean
  'deploy'?`).

Enable the prompt on the template (see the export/apply format in
[specs.md](specs.md)), or drop the field. A field the preflight does not know
that AWX still ignores fails its case as `error` at launch, and the job AWX
launched anyway is cancelled (unless `--no-cancel`).

## Commands

```bash
untaped awx test init "Deploy app"                # starter suite from the survey and prompts
untaped awx test init "Deploy app" --out suites/deploy.yml
untaped awx test init Release --workflow          # starter suite for a workflow
untaped awx test validate                         # every suite, no launch
untaped awx test list --var env=prod              # the cases that would run
untaped awx test run --scm-branch HEAD --format json
untaped awx test run --case deploy-smoke/web --case db --non-interactive
untaped awx test run suites/deploy.yml --var env=prod --parallel 2 --show-logs
untaped awx test run --scm-branch HEAD --compare baseline.json --format json
untaped awx test run --scm-branch HEAD --baseline main --format json
untaped awx test validate --source-ref HEAD       # the copies a run would create
untaped awx test run --source-ref HEAD --format json
untaped awx test run --source-ref v1.4.0 --keep --case deploy-smoke/web
untaped awx test prune --dry-run                  # leftover copies older than 2h
untaped awx test prune --run k3x9 --older-than 0 --yes   # one ended run's copies
untaped awx schema AwxTestSuite                   # the body's JSON Schema
```

- `init TEMPLATE` reads the template's launch prompts and survey (the same
  reads as the preflight) and writes a commented suite with one `smoke`
  case. Required survey variables get their default, else their first
  choice, else `TODO`. A password with a stored default gets `$encrypted$`,
  which makes AWX use the stored value; one without gets `TODO` (supply it
  from a `secret` suite variable, never write it in the file). Optional survey
  variables and the launch fields the template prompts for are listed as
  comments. It writes `.untaped/awx/tests/<name>.yml` at the git root (the
  template name lowercased, `-` between words), or `--out PATH`; it refuses
  to replace an existing file, prints the path on stdout and suggests
  `validate`. `--organization` scopes the template lookup. With `--workflow`,
  TEMPLATE is a workflow: the suite names `workflowTemplate`, a comment lists
  its node ids (approvals marked), the `smoke` case carries a commented
  `nodes:` example, and a commented `approvals: approve` when the workflow has
  approval nodes (uncomment it, or a pending approval fails the case).
- `--case` selects `CASE` (in every suite) or `SUITE/CASE`, and is
  repeatable; a `--case` that matches nothing is an error before any launch.
- `run --dry-run` checks the selected cases as `validate` does (with
  `--scm-branch` or `--source-ref` applied) and launches nothing.
- `--scm-branch REF` runs every job on that branch, tag or commit. Each
  template must prompt for it (`ask_scm_branch_on_launch`, which AWX allows
  only when the project allows branch override). `--scm-branch HEAD` is the
  current branch as named on its upstream remote; it is refused while HEAD is
  detached, has no upstream, or is not pushed.
- `list` emits one `awx.test_case` row per case (`suite`, `case`,
  `job_template`, `workflow_template`, `organization`, `path`, `variables`);
  the table shows `suite`, `case`, and `job_template` or
  `workflow_template` when a suite sets it.
- `validate` prints `SUITE/CASE: problem` on stderr per failing case and
  exits 1, else reports `N cases validated`. It also warns (without failing)
  about each case that expects `status: failed` without `failed_tasks`, and
  each workflow case without `approvals` whose workflow has approval nodes.
- `--compare FILE` compares the run with the saved output of an earlier
  run, and `--baseline REF` first runs every selected case on `REF` (as
  `--scm-branch`) to compare with; see
  [test-results.md](test-results.md#comparing-with-a-baseline).
- `run` results are described in [test-results.md](test-results.md).
