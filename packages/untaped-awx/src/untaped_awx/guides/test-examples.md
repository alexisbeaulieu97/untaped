---
description: "Read when starting an awx test suite: five working suites (smoke, variants, negative, idempotent, workflow) to copy into .untaped/awx/tests/ and edit."
---

Each suite below loads as written. Copy one to `.untaped/awx/tests/` in the
playbook repository, change its template and cases, then run `untaped awx
test validate`. The rules behind them: `untaped agents awx test-suites` and
`untaped agents awx test-cases`.

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
