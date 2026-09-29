# awx test workflow suites reuse the job case; a workflow is blamed on its failing node

Decision ID: `dec_01a0eebbe4e37457a4975c6fd4c3bfb8`

A change to a job template often matters only through the workflows that run
it, and a workflow can stop on an approval nobody answers. An agent must test
a workflow unattended, and read which node failed and why.

- No new document kind: a suite names `workflowTemplate` instead of
  `jobTemplate` (exactly one), and a case keeps its shape. The binding of a
  suite to the template it launches is one replaceable step, so a run can
  bind it to a temporary copy.
- Per-node checks are the job case's checks (one shared model, one checking
  code path) keyed by AWX's node `identifier`, plus `status: never_ran`.
  Unknown ids fail the preflight, with the closest ids.
- Approvals are declared per case (`approve` or `deny`), never implied: a
  pending approval without an answer fails fast as the suite's problem and
  cancels the workflow, so a run never hangs.
- A failed workflow is attributed to the node that failed it (a failure with
  no failure or always path), by the job rules applied to that node's job,
  recursing into nested workflows up to a depth cap. A denial the case asked
  for is the expectation's; one from outside the run is the controller's.
- Node jobs are read only when a check or the attribution needs them: the
  row's `nodes` come from one read of the workflow's nodes, and a workflow's
  host summaries (its node jobs', summed) are read only for a check.

## Related decisions

- Builds on: [awx test regression tools](dec_01a0ee7aa1b0734c84536654eaf40cb0-v4-awx-test-regressions-fail-only-on-a-regression.md)
- Builds on: [awx test failures name the responsible system](dec_01a0ee3c7ace70f6afb9ccbbac9d6d93-v4-awx-test-failures-name-the-responsible-system.md)
