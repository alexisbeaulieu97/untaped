"""The option bundles of ``awx test``, each holding the guide to read before using it.

A bundle's docstring is its guide (``untaped docs awx guides test-suites``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from untaped.sdk import ExistingFile, KeyValue, Short


class TestSuites(BaseModel):
    """Read before writing or editing an awx test suite: where suites live, the header and
    Jinja2 body, variables, workflow suites and approvals, and what the preflight
    refuses.

    A test suite launches one job template (or one workflow) once per case, each
    with its own launch payload, and checks each job against what the case
    expects. `untaped awx schema AwxTestSuite` lists every field as JSON Schema;
    this page covers the rules it can't say. A case's own fields are in
    `untaped docs awx guides test-cases`, and working suites to start from in
    `untaped docs awx guides test-examples`.

    ## Where suites live

    - Suites live in the repository they test, under `.untaped/awx/tests/` at the
      root of the git checkout; `untaped awx test init` writes one there.
    - Without paths, `test run`, `test list` and `test validate` read every suite
      under that directory (the current directory outside a git checkout).
    - A directory is searched recursively: every `*.yml`/`*.yaml` file with a
      `kind: AwxTestSuite` key is a suite.
    - Hidden files and directories are skipped and directory symlinks are not
      followed, so vars files and fixtures can sit beside the suites.
    - A file named directly must be a suite. Suite names must be unique across
      the files read.

    ## Header and body

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

    Rendering:

    - An undefined variable is an error, never an empty string.
    - Every Jinja2 construct works; loops build case matrices (the variants
      example).
    - `{{ value | to_json }}` writes JSON (valid YAML, strings quoted) and
      `{{ value | to_yaml }}` a one-line YAML value. Use them for strings that
      might contain `:` or `#`, and for lists and mappings.
    - Escape text meant for AWX that looks like Jinja2: `{% raw %}{{ ansible_host
      }}{% endraw %}` or `{{ '{{' }} ansible_host }}`.
    - The rendered body must be a YAML mapping. A key repeated in one mapping
      (two cases a loop gave the same name) is an error, not an overwrite.
    - The `!ref` tag works anywhere in the body.

    ## Variables

    Every field of a variable is optional, and a variable without a `default` is
    required. Values come from, highest precedence first: `--var`, `--vars-file`
    (a later file wins), the `default`, then an interactive prompt for a required
    variable.

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

    ```console
    $ untaped awx test run --vars-file ~/.secrets/deploy-test.yml --non-interactive
    ```

    ## The body

    `cases` needs at least one case. Unknown keys are errors everywhere in the
    body, so a typo such as `expected:` fails validation instead of being
    ignored. `untaped awx test list` reports
    the header's declarations under `variables`.

    ## Workflow suites

    `workflowTemplate: NAME` makes every case launch a workflow job template.
    Cases are written as for a job template, except:

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
      (`untaped docs awx commands test`).

    `expect.nodes` checks each named node's job with a case's own checks:

    - Each key is a node id (AWX's node `identifier`, the `id` of an exported
      node; `untaped awx test init NAME --workflow` lists them).
    - A key the workflow does not have fails the preflight with the closest ids.
    - A node of a nested workflow is not addressed: check the node that runs
      that workflow.
    - An entry merges over `defaults.expect.nodes` as a case's `expect` does,
      except that `status: never_ran` replaces the default's entry whole.
    - An entry takes the checks of a case except `idempotent`; `status:
      never_ran` is for a node the workflow did not run, and no other check goes
      with it.
    - A node that ran an approval or a management job has only a status: an
      approved approval is `successful`, a denied or timed-out one `failed`.
    - A node that ran a nested workflow is checked as a workflow (no `log`).
      Nested workflows are followed 5 levels deep.
    - A negative case whose cause is named in a node needs no `failed_tasks` of
      its own.

    ## Preflight

    `validate` renders, parses and resolves every case and checks it against its
    template without launching; `run` does the same first and launches nothing
    when any case fails. A case fails preflight when:

    - the file does not render or validate;
    - the template, or a name in `launch` or `!ref`, is not found or is
      ambiguous;
    - a required survey variable is missing from `launch.extra_vars`;
    - a launch field is one the template does not prompt for (its
      `ask_*_on_launch` is false), or, with a survey and no
      `ask_variables_on_launch`, an extra var the survey does not ask; a value
      the template already has is a no-op, left out of the launch;
    - a workflow case checks a node id the workflow does not have.

    Enable the prompt on the template, or drop the field. A field the preflight
    does not know that AWX still ignores fails its case as `error` at launch, and
    the job AWX launched anyway is cancelled (unless `--no-cancel`).
    """

    var: list[KeyValue] = Field(
        default_factory=list,
        description="KEY=VALUE (repeatable); wins over --vars-file and the default.",
    )
    vars_file: list[Path] = Field(
        default_factory=list,
        description="YAML file of variable values (repeatable; a later file wins).",
    )
    non_interactive: bool = Field(
        False, description="Fail on missing required vars instead of prompting."
    )


class TestCases(BaseModel):
    """Read before writing a case of an awx test suite: how launch names and defaults
    merge, !ref, how expect checks merge and match (hosts, failed_tasks, idempotent),
    and timeouts.

    What one case of a suite holds. `untaped awx schema AwxTestSuite` lists a
    case's fields; the suite around it is in `untaped docs awx guides test-suites`.

    ## `launch`

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

    ## `expect`

    Every check must hold. Against `defaults.expect`:

    - a case's `status`, `changed`, `idempotent` and `failed_tasks` replace the
      default's;
    - each of its `log` lists replaces the same list;
    - each counter a `hosts` entry sets replaces the default's for that host;
    - whatever the case leaves out is inherited.

    `status: failed` tests an intended failure: add `failed_tasks` naming the
    task and message that prove why (the negative example). `validate` warns
    about a `status: failed` case without them.

    Log, `changed`, `hosts` and `failed_tasks` checks read what AWX writes from
    the job's events, so they wait until AWX has saved them. A job AWX is still
    saving is an `awx.controller` error (exit 5, retry later), never a pass.

    ### `hosts`

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
    - A named host must be in the job's host summaries, so a misspelt host fails
      its check.
    - A job on more than 500 hosts keeps 500 in the result, but the checks read
      every host over a bound, so no host past the cut can hide a failure.

    ### `failed_tasks`

    An entry matches a failed task, as the result's
    `failure.evidence.failed_tasks` lists them:

    - An entry needs at least one of `task`, `msg` and `matches`.
    - Every entry must match some failed task; other failed tasks do not fail
      the check.
    - `ignore_errors` failures and failures a `rescue` block handled do not
      count.
    - An entry matched only by a failure the host summaries cannot show was
      unhandled is not proven: an `awx.expectation` error (exit 1).
    - A job that succeeded has no failed task, so its entries fail.

    ### `idempotent`

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

    How a rerun fails: `untaped docs awx guides test-results`.

    ## Timeouts and parallelism

    - A job still running at the case's timeout, or when polling fails or Ctrl-C
      stops the run, is cancelled; `--no-cancel` leaves it running.
    - `--parallel N` (default `awx.test_parallel`) runs that many cases at once.
    """

    case: list[str] = Field(
        default_factory=list,
        description="Only this case, as CASE (in every suite) or SUITE/CASE (repeatable).",
    )


class TestResults(BaseModel):
    """Read when an awx test run exits non-zero or compares with a baseline: what the exit
    code means, which system a failure blames and what to do, how reruns and workflow
    cases fail, and each baseline change.

    `untaped awx test run` prints one `awx.case_result` row per case, in the
    order the cases are declared, then a summary on stderr
    (`4 cases: 3 pass, 1 fail`). Read the rows with `--format json` (or `yaml`
    or `pipe`): the table leaves out the evidence and the host summaries.
    `untaped plugin schema awx.case_result` describes every field.

    A failing row, abridged:

    ```json
    {
      "suite": "deploy", "case": "prod-like", "result": "fail",
      "job_status": "error", "job_id": 4410,
      "failure": {
        "system": "awx.scm", "category": "failed", "retryable": false,
        "message":
          "project update 812 for 'acme-playbooks' failed: couldn't find remote ref feature/x",
        "hint":
          "push the branch, …; read its log: `untaped awx jobs logs 812 --kind project_update`",
        "evidence": {
          "related": {
            "kind": "project_update", "id": 812, "name": "acme-playbooks",
            "status": "failed", "url": "…"
          },
          "log_tail": [
            "fatal: [localhost]: FAILED! => {\"msg\": \"couldn't find remote ref feature/x\"}"
          ]
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
      permission, credential lookup), `awx.inventories` (a failed source sync),
      `git` (HEAD not pushed, or an unusable checkout) or `local` (missing
      settings such as `awx.server.url`).
    - 5: temporary, retry later: `awx.controller` or `awx.hosts`.
    - 130: interrupted. Running jobs are cancelled and listed on stderr; with
      `--no-cancel` they keep running and the list ends with an
      `untaped awx jobs wait …` command.

    With `--format json`, stderr is JSON Lines: an error that stops the run has
    `category`, `system`, `retryable`, `hint` and `exit_code`. With a baseline,
    a failure the baseline already had does not fail the run (below).

    ## Which system is responsible

    Every case that did not pass has a `failure` whose `system` says who must
    act. The first row that matches decides:

    - `awx.suite`, `invalid`, `not_found` (1): AWX refused the launch as the case asked it: an
      unknown name, an ignored field, a missing survey variable. Fix the suite or enable the prompt
      on the template, then `untaped awx test validate`.
    - `awx.credentials`, `auth`, `permission` (4): AWX rejected the token or a permission, or a
      credential lookup ended the job (or its update) in `error`. Fix the token (`untaped awx ping`)
      or the credential in AWX; leave the playbook alone.
    - `awx.scm`, `failed` (1); `invalid` before launching: A `project_update` failed (see below).
      Push the branch (`git push -u origin HEAD`) or fix the ref; read `evidence.related` and its
      log tail.
    - `awx.inventories`, `config` (4): AWX names a failed `inventory_update` in the job's
      explanation. The inventory source is broken: read its log (`untaped awx jobs logs ID --kind
      inventory_update`).
    - `awx.controller`, usually `unavailable` (5); a job AWX no longer finds is `not_found` (1): AWX
      itself failed (see below). Retry later; `evidence.job_explanation` and
      `evidence.result_traceback` say what AWX saw.
    - `awx.expectation`, `failed` (1): The job ran as intended but an expectation did not hold (see
      below). Compare `expectations` with what the job did; fix the change or the case.
    - `awx.hosts`, `unavailable` (5): The job failed and every failed task is an unreachable host.
      Retry once the hosts in `evidence.unreachable_hosts` are reachable.
    - `awx.playbook`, `failed` (1): The job failed any other way, or was still running at the
      timeout (a hang). Fix the playbook, role or variables: `evidence.failed_tasks` names the host,
      task and message.

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
    - a check that needed the job's events while AWX was still saving them
      (`AWX is still saving the events of job 4412, …: only its status was
      checked`), never checked on partial data;
    - a job that never left `pending`/`waiting` before the timeout.

    `awx.expectation` covers:

    - the job succeeded where the case expects a failure;
    - a log check, a `changed` or `hosts` bound, or a `failed_tasks` entry
      failed;
    - an `idempotent` rerun changed something;
    - `failed_tasks not proven: …`: an entry matched only by a failure the host
      summaries cannot show was unhandled. A rerun reads the same recap, so
      instead of retrying, expect a task that fails unhandled, or check the log.

    Its `failure.message` joins the failed checks, such as `expected <= 0
    changed tasks, got 3; no log line contains 'PLAY RECAP'`. A check whose data
    could not be read makes the case an `error` when nothing else failed, and a
    `note` otherwise.

    `awx.playbook` also covers a failure with no failed task at all (a syntax
    error or a missing role, shown in the log tail).

    A failure that is not AWX's keeps its own `system`: `untaped` for a bug in
    untaped, `local` for local setup. Before blaming the change, confirm the job
    ran it: `scm_revision` must be the commit you pushed.

    `failed_tasks` in the evidence leaves out `ignore_errors` failures and
    failures a `rescue` block handled: a host that counts N failures failed on
    its last N failed tasks, the ones before were rescued (likewise for
    `unreachable`). When a host has no summary, or its counters do not account
    for its failed tasks, all of them are listed. It is `null` when the events
    could not be read or were still being saved; read them later with `untaped
    awx jobs events ID`.

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
      `untaped docs awx commands test`): `awx.credentials` (exit 4).
    - A node that ran a project or inventory update itself and failed is
      `awx.scm` or `awx.inventories`, whatever the case expects.

    ## Comparing with a baseline

    `--compare FILE` compares the run with the saved `--format json` output
    of an earlier run; `--baseline REF` runs every selected case on `REF`
    first and compares with that (an `idempotent` case then launches 4 jobs).

    - The baseline run exits 1 when the base branch already fails some cases;
      those become `still_failing` rows.
    - `--compare` and `--baseline` cannot be combined (exit 2). A missing file
      exits 2; a file that is not such output fails before any launch (exit 1).

    Rows are matched by `suite` and `case`, and each gains `baseline` and
    `change`:

    - `regression` (fails the run: yes): It passed in the baseline and does not now, or it now fails
      because of another `system` (or in another workflow node).
    - `unverified` (fails the run: yes): It fails now, and the baseline failed because of the
      environment (`auth`, `permission`, `config` or `unavailable`), so it proves nothing.
    - `new` (fails the run: when it failed): The baseline did not run it; it counts as in a run
      without a baseline.
    - `still_failing` (fails the run: no, unless 4 or 5): It fails now as in the baseline: same
      `system` and failing `node` (a baseline without a system matches on the result).
    - `fixed` (fails the run: no): It did not pass in the baseline and passes now.
    - `pass` (fails the run: no): It passed in both.
    - `removed` (fails the run: no): The baseline ran it and this run did not (a case `--case`
      leaves out is not listed). Only `suite`, `case`, `baseline` and `change` are set.

    The exit code is then 0 when no case regressed and no new case failed, and 1
    for a `regression`, an `unverified` row or a failing `new` case. A higher
    code still wins: 4 or 5 for any case whose failure is the environment's or
    temporary, in either run. The stderr summary adds a line counting each
    change (`compared with the baseline: 1 regression, 1 fixed, 3 pass`).

    ## Verdicts

    - `pass`: The job reached a terminal status and every expectation held (and no update it waited
      for failed).
    - `fail`: The job finished but did not do what the case expects; `failure.system` says who is
      responsible.
    - `error`: untaped could not launch, follow or read the job: AWX refused the launch or ignored
      fields (the case keeps any `job_id`), polling failed, or a needed log could not be downloaded.
    - `timeout`: The job was unfinished at the case's timeout and was cancelled unless
      `--no-cancel`. Read `evidence.log_tail` for where it hung; raise `timeout` only when the job
      is legitimately slow.

    ## Follow-ups

    ```console
    $ untaped awx test run --case deploy-smoke/web --format json
    $ untaped awx test run --show-logs
    $ untaped awx jobs logs 4410 --grep 'fatal:'
    $ untaped awx jobs logs 812 --kind project_update
    $ untaped awx jobs events 4410 --filter event=runner_on_failed
    ```
    """

    show_logs: bool = Field(
        False,
        description=(
            "Print the failure, failed tasks and log tail of each case that did not pass to stderr."
        ),
    )
    compare: ExistingFile | None = Field(
        None,
        description=(
            "Compare with an earlier `awx test run --format json` (or pipe) output: each row "
            "gains baseline and change, and a failure the baseline already had does not fail "
            "the run."
        ),
    )
    baseline: str | None = Field(
        None,
        description=(
            "Run every case on this branch, tag or commit first (as --scm-branch; HEAD once "
            "pushed), then as asked, and compare as --compare does."
        ),
    )


class TestExamples(BaseModel):
    r"""Read when starting an awx test suite: five working suites (smoke, variants,
    negative, idempotent, workflow) to copy into .untaped/awx/tests/ and edit.

    Each suite below loads as written. Copy one to `.untaped/awx/tests/` in the
    playbook repository, change its template and cases, then run `untaped awx
    test validate`. The rules behind them: `untaped docs awx guides test-suites` and
    `untaped docs awx guides test-cases`.

    ## Smoke

    ```yaml
    # The smallest useful suite: launch the job template once and expect the job
    # to succeed. Copy it to .untaped/awx/tests/ in the playbook repository and
    # change jobTemplate.
    kind: AwxTestSuite
    name: deploy-smoke
    jobTemplate: Deploy app
    cases:
      smoke:
        expect:
          status: successful
          log:
            contains: ["PLAY RECAP"]
    ```

    ## Variants

    ```yaml
    # Variants: header variables, a case matrix built with Jinja2, defaults every
    # case inherits, !ref, and per-case limits. Only comments and blank lines may
    # come before the header.
    #
    #   untaped awx test run variants.yml --var env=prod
    #   untaped awx test run variants.yml --var regions=us-east
    ---
    variables:
      env:
        type: choice
        choices: [staging, prod]
        default: staging
        description: Environment to test against
      regions:
        type: list
        default: [us-east, eu-west]
    ---
    kind: AwxTestSuite
    name: deploy-variants
    jobTemplate: Deploy app
    # The template's organization; launch names (credentials, inventories, and
    # !ref without an organization) are looked up in it too.
    organization: Ops
    defaults:
      timeout: 1800
      launch:
        # Merged into every case: a case's extra_vars are deep-merged over these,
        # and its credentials are added to these.
        extra_vars:
          dry_run: true
          app: {name: web, version: "1.4"}
        credentials: [Vault token]
      expect:
        log:
          not_contains: ["[DEPRECATION WARNING]"]
    cases:
    {% for region in regions %}
      {{ region }}:
        launch:
          limit: "{{ region }}-{{ env }}"
          inventory: !ref {kind: Inventory, name: "{{ env }} inventory"}
          extra_vars:
            app: {version: "1.5"}
            region: {{ region | to_json }}
            # A !ref with its own scope looks elsewhere.
            shared_project_id: !ref {kind: Project, name: Shared playbooks, organization: Default}
        expect:
          log:
            contains: ["PLAY RECAP"]
            matches: ['{{ region }}-\S+ +: ok=\d+ +changed=\d+ +unreachable=0 +failed=0']
    {% endfor %}
    ```

    ## Negative

    ```yaml
    # Negative cases: the playbook must refuse bad input, and fail for that reason.
    # `status: failed` alone would also pass on an unrelated failure, so each case
    # names the failed task and its message (`untaped awx test validate` warns
    # about a `status: failed` case without `failed_tasks`).
    kind: AwxTestSuite
    name: deploy-validation
    jobTemplate: Deploy app
    defaults:
      expect:
        status: failed
        hosts:
          "*": {unreachable: 0}
    cases:
      missing-version:
        launch:
          extra_vars: {app_version: ""}
        expect:
          failed_tasks:
            - task: Validate input
              msg: app_version must be set
      unknown-environment:
        launch:
          extra_vars: {app_version: "1.4", environment: nowhere}
        expect:
          failed_tasks:
            - task: Validate input
              matches: 'environment must be one of \[?staging'
      valid-input:
        # The control case: the same template with good input still succeeds.
        launch:
          extra_vars: {app_version: "1.4", environment: staging}
        expect:
          status: successful
    ```

    ## Idempotent

    ```yaml
    # Idempotence and host bounds: a converged host must stay converged.
    # `idempotent: true` launches the case again once it passed; the rerun must
    # succeed with no changed task on any host, or the case fails and lists the
    # tasks the rerun changed (failure.evidence.changed_tasks). Host bounds read
    # the job's host summaries (PLAY RECAP counters).
    kind: AwxTestSuite
    name: deploy-converge
    jobTemplate: Deploy app
    defaults:
      launch:
        extra_vars: {app_version: "1.4", environment: staging}
      expect:
        status: successful
        hosts:
          "*": {failed: 0, unreachable: 0}
    cases:
      converge:
        launch:
          limit: web
        expect:
          idempotent: true
      already-deployed:
        # The web hosts already run 1.4, except the canary, which gets its new
        # config. The case's "*" bound adds `changed` to the defaults' `failed`
        # and `unreachable` bounds; the canary's own `changed` bound wins over it.
        launch:
          limit: web
        expect:
          changed: 2
          hosts:
            "*": {changed: 0}
            web-canary: {changed: 2}
    ```

    ## Workflow

    ```yaml
    # A workflow suite: launch a workflow job template, answer its approval, and
    # check what each node's job did. Node ids are the workflow's node ids
    # (`untaped awx test init Release --workflow` lists them). A failed workflow is
    # blamed on the node that failed it: failure.message starts `node <id>:`.
    kind: AwxTestSuite
    name: release
    workflowTemplate: Release
    defaults:
      approvals: approve
      launch:
        extra_vars: {app_version: "1.4"}
      expect:
        nodes:
          # Every case's deploy node must leave no host failed or unreachable.
          deploy:
            hosts:
              "*": {failed: 0, unreachable: 0}
    cases:
      happy:
        expect:
          status: successful
          nodes:
            deploy:
              log: {contains: ["app_version=1.4"]}
            rollback: {status: never_ran}
      converges:
        # The whole workflow runs twice; the second run must change nothing.
        expect:
          idempotent: true
      bad-version:
        # Deploy refuses the version and its failure path rolls back, so the
        # workflow itself succeeds; the deploy node must fail for that reason.
        launch:
          extra_vars: {app_version: "not-a-version"}
        expect:
          status: successful
          nodes:
            deploy:
              status: failed
              hosts:
                "*": {failed: 1}
              failed_tasks:
                - task: Validate input
                  msg: app_version
            rollback: {status: successful}
      denied:
        # Denying the approval stops the release before deploy; with no failure
        # path out of the approval node, the workflow fails. `never_ran` replaces
        # the defaults' deploy checks whole.
        approvals: deny
        expect:
          status: failed
          nodes:
            approve: {status: failed}
            deploy: {status: never_ran}
    ```
    """

    out: Annotated[Path | None, Short("-o")] = Field(
        None,
        description=(
            "Write the suite to this file (default: .untaped/awx/tests/TEMPLATE.yml at the git "
            "checkout root, the name lowercased with - between words); an existing file is "
            "never replaced."
        ),
    )
    workflow: bool = Field(
        False,
        description=(
            "TEMPLATE is a workflow job template: the suite names workflowTemplate and lists "
            "the workflow's node ids and approval nodes."
        ),
    )


__all__ = ["TestCases", "TestExamples", "TestResults", "TestSuites"]
