# awx test temporary test sets: repo specs are copied per run, pinned, and always torn down

Decision ID: `dec_01a0ef86eb4476678fca251bb6e6d6b4`

A branch that changes a template or workflow cannot be tested against what
AWX holds, and an agent that tests `main`'s configuration by mistake "fixes"
the wrong thing. `awx test run --source-ref REF` tests the ref with the
configuration it carries.

- **Bound by name, no new suite key.** A suite whose template name and
  organization match a repo spec (read at the pinned commit through the
  reader `apply --source-ref` uses) runs a copy of it; a copied workflow's
  nodes run the copies of the templates with specs.
- **Copies are applied, not templated.** Specs stay plain export documents;
  the harness applies the run-specific name, the description marker,
  `scm_branch` = the commit and the launch prompts the cases need, through
  the apply engine, so links resolve by name exactly as `apply` does and are
  never created.
- **Nothing silently tests another commit.** Every template the run launches
  without copying it (a suite's, or one a workflow node runs) must prompt
  for `scm_branch`, or the run is refused before any write.
- **Provisioning is not a test result.** Every check a write would fail runs
  before the first write, in `validate` too, and a failure stops the run
  before any launch with its own attribution (the spec's or suite's, or the
  controller's).
- **Teardown always runs and never fails a case.** Copies are found by name
  plus marker (not labels, which apply never creates), so a copy made before
  an interrupt, or left by a killed run, is found too; what is left is a
  warning with the exact `prune --run` command.
- Provisioning needs no confirmation, like the launches a run makes; its cost
  shows in `validate --source-ref`. `test run` stdout stays `awx.test_result`
  rows, which `--compare` reads.

## Related decisions

- Builds on: [AWX specs are plain export documents in the repo](dec_01a0eb415a9e7195873ec9f8a91da3dd-v4-awx-specs-are-plain-export-documents-in-the-repo.md)
- Builds on: [awx test workflow suites reuse the job case](dec_01a0eebbe4e37457a4975c6fd4c3bfb8-v4-awx-workflow-suites-reuse-the-job-case.md)
