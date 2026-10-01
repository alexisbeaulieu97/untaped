# awx test workflow suites reuse the job case; a workflow is blamed on its failing node

Decision ID: `dec_01a0eebbe4e37457a4975c6fd4c3bfb8`

A change to a job template often matters only through the workflows that run
it, and a workflow can stop on an approval nobody answers. An agent must test
a workflow unattended, and read which node failed and why.

- No new document kind: a suite names `workflowTemplate` instead of
  `jobTemplate` (exactly one), and a case keeps its shape. A suite binds to
  the template it launches in one place (`Suite.binding`: kind, name,
  scope), so a run that provisions a temporary copy binds each suite once
  and uses that binding everywhere the template is named.
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
  Only the same failing node keeps a baseline failure `still_failing`.

## Related decisions

- Builds on: [awx test regression tools](awx-test-regressions-fail-only-on-a-regression.md)
- Builds on: [awx test failures name the responsible system](awx-test-failures-name-the-responsible-system.md)
