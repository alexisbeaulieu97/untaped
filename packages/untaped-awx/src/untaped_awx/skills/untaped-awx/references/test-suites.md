# Test suites (`AwxTestSuite`)

A test suite launches one job template (or one workflow) once per case, each
with its own launch payload, and checks each job against what the case
expects. `untaped awx schema AwxTestSuite` lists every field (as JSON Schema,
for editors and validators); this page covers the rules it does not. The
[examples](../examples/) are working starting points.

- [Where suites live](#where-suites-live)
- [File layout: header and body](#file-layout-header-and-body)
- [Header: `variables`](#header-variables)
- [Body: the suite](#body-the-suite)
- Cases (`launch`, `!ref`, `expect`, timeouts): [test-cases.md](test-cases.md)
- [Workflow suites](#workflow-suites)
- Temporary test sets with `--source-ref`: [source-ref.md](source-ref.md)
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
- The `!ref` tag works in the body (see [`!ref`](test-cases.md#ref-a-resource-by-name)).

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
least one. Set `organization` when the template's name is not unique (default:
`awx.default_organization`). `variables` is not written
in the body: `untaped awx test list` reports the header's declarations under
that key.

Unknown keys are errors everywhere in the body, so a typo such as
`expected:` fails validation instead of being ignored.

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
