# Test suites (`AwxTestSuite`)

A test suite launches one job template several times, once per case, with a
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
  a line `kind: AwxTestSuite` is a suite. Hidden files and directories are
  skipped and directory symlinks are not followed, so vars files and fixtures
  can sit beside the suites. A file named directly must be a suite.
- Each file is read once, and suite names must be unique across the files
  read.

## File layout: header and body

A suite file is YAML. It may open with a header: a YAML mapping between two
`---` lines, which must be the first thing in the file (comments go inside
it). The rest, the body, is a Jinja2 template rendered with the header's
variables and then parsed as YAML:

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

A file without a header is just the body.

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

## Body: the suite

| Field | Meaning |
|---|---|
| `kind` | Required, exactly `AwxTestSuite`. |
| `name` | The suite name used by `--case SUITE/CASE`; default: the file name without its extension. |
| `jobTemplate` | Required. The name of the job template every case launches. |
| `organization` | The job template's organization when its name is not unique (default: `awx.default_organization`). |
| `defaults` | A case body every case inherits (`launch`, `expect`, `timeout`). |
| `cases` | Required, at least one. Case name → case body; each case launches the job template once. |
| `variables` | Not written in the body: `untaped awx test list` reports the header's declarations under this key. |

Unknown keys are errors everywhere in the body, so a typo such as
`expected:` or `jobtemplate:` fails validation instead of being ignored.

## Case body

| Field | Meaning |
|---|---|
| `launch` | The AWX launch payload for this case (see below). |
| `expect` | What the job must produce (see below). |
| `timeout` | Seconds (a positive number) to wait for the job; then it is cancelled and the case is `timeout`. |

### `launch`: the launch payload

`launch` holds the fields of AWX's job template launch request:

- `extra_vars` (a mapping), `limit`, `inventory`, `credentials` (a list),
  `scm_branch`, `job_tags`, `skip_tags`, `job_type` (`run` or `check`),
  `verbosity` (0-4), `diff_mode`;
- `execution_environment`, `labels`, `instance_groups`, `forks`, `timeout`
  (AWX's job timeout, not the case's wait) and `credential_passwords`.

A field outside this list is sent anyway with a warning
(`unknown launch field 'extra_var' — typo?`).

`inventory`, `credentials`, `execution_environment`, `labels` and
`instance_groups` take names: each name is looked up (organization-scoped
kinds in the suite's organization, else `awx.default_organization`). An
integer is used as an AWX id as it is. A single name where a list is expected
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
(`organization: Ops`); without them an organization-scoped kind is looked up
in `awx.default_organization`. Plain mappings are never treated as references.

### `expect`: what the job must produce

| Field | Meaning |
|---|---|
| `status` | The job's final status: `successful` (the default), `failed`, `error` or `canceled`. |
| `log` | Checks on the job's full stdout, line by line. |
| `log`: `contains` | Texts that some line must contain. |
| `log`: `not_contains` | Texts that no line may contain. |
| `log`: `matches` | Python regular expressions that some line must match (searched anywhere in the line; an invalid pattern fails validation). |

Every check must hold. A case's `status` replaces the default's, and each of
its `log` lists replaces the same list in `defaults.expect`; whatever the case
leaves out is inherited. `status: failed` tests an intended failure: add a
`log.contains` or `log.matches` for the message that proves the job failed
for the right reason (see `negative.yml`).

### Timeouts and parallelism

A case waits `--timeout SECONDS` when given, else its own `timeout`, else
`defaults.timeout`, else `awx.test_timeout` (1800 seconds). A job still
running then is cancelled and the case is `timeout`; `--no-cancel` leaves it
running. `--parallel N` (default `awx.test_parallel`, 4) runs that many cases
at once. A polling error or Ctrl-C cancels the job too (unless
`--no-cancel`).

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
  would ignore it), unless the value equals the template's own;
- the template has a survey but does not prompt for variables, and
  `extra_vars` holds a variable outside the survey.

Enable the prompt on the template (see the export/apply format in
[specs.md](specs.md)), or drop the field. A field the preflight does not know
that AWX still ignores fails its case as `error` at launch.

## Commands

```bash
untaped awx test init "Deploy app"                # starter suite from the survey and prompts
untaped awx test init "Deploy app" --out suites/deploy.yml
untaped awx test validate                         # every suite, no launch
untaped awx test list --var env=prod              # the cases that would run
untaped awx test run --scm-branch HEAD --format json
untaped awx test run --case deploy-smoke/web --case db --non-interactive
untaped awx test run suites/deploy.yml --var env=prod --parallel 2 --show-logs
untaped awx schema AwxTestSuite                   # the body's JSON Schema
```

- `init TEMPLATE` reads the template's launch prompts and survey (the same
  reads as the preflight) and writes a commented suite with one `smoke`
  case. Required survey variables get their default, else their first
  choice, else `TODO`; passwords always get `TODO` (supply them from a
  `secret` suite variable, never write them in the file). Optional survey
  variables and the launch fields the template prompts for are listed as
  comments. It writes `.untaped/awx/tests/<name>.yml` at the git root (the
  template name lowercased, `-` between words), or `--out PATH`; it refuses
  to replace an existing file, prints the path on stdout and suggests
  `validate`. `--organization` scopes the template lookup.
- `--case` selects `CASE` (in every suite) or `SUITE/CASE`, and is
  repeatable; a `--case` that matches nothing is an error before any launch.
- `--scm-branch REF` runs every job on that branch, tag or commit. Each
  template must prompt for it (`ask_scm_branch_on_launch`, which AWX allows
  only when the project allows branch override). `--scm-branch HEAD` is the
  current branch as named on its upstream remote; it is refused while HEAD is
  detached, has no upstream, or is not pushed.
- `list` emits one `awx.test_case` row per case (`suite`, `case`,
  `job_template`, `organization`, `path`, `variables`); the table shows
  `suite`, `case` and `job_template`.
- `validate` prints `SUITE/CASE: problem` on stderr per failing case and
  exits 1, else reports `N cases validated`.
- `run` results are described in [test-results.md](test-results.md).
