# awx test temporary test sets: repo specs are copied per run, pinned, and always torn down

Decision ID: `dec_01a0ef86eb4476678fca251bb6e6d6b4`

A branch that changes a template or workflow cannot be tested against what
AWX holds, and an agent that tests `main`'s configuration by mistake "fixes"
the wrong thing. `awx test run --source-ref REF` tests the ref with the
configuration it carries.

- **Bound by name, no new suite key.** A suite whose template name and
  organization match a repo spec (read at the pinned commit through the
  git-backed reader `apply --source-ref` uses) runs a copy of it; a copied
  workflow's nodes run the copies of the templates with specs. Each suite
  is bound once (`TemplateBinding`, pinned) and the runner uses that
  binding everywhere, prefetch included.
- **Copies are applied, not templated.** Specs stay plain export documents;
  the harness applies the run-specific name `NAME [untaped-test SHA RUN]`,
  the description marker, `scm_branch` = the commit, the prompts for every
  field a case sets, and the renamed node references, and drops webhook
  settings. Creation goes through the apply engine, so links resolve by name
  exactly as `apply` does and are never created; a name taken in the scope is
  refused.
- **Nothing silently tests another commit.** A template the run does not copy
  must prompt for `scm_branch` or the run is refused before any write; a
  case's own `scm_branch` is replaced; `--scm-branch` and `--baseline` do not
  combine with it.
- **Provisioning is not a test result.** Every check that a write would fail
  (links, names, the project's branch override, templates that would not run
  the commit) runs before the first write, in `validate` too. A failure stops
  the run before any launch, with its own attribution: the suite's or spec's
  (`awx.suite`, `awx.scm`, exit 1) or the controller's
  (`awx.credentials` 4, `awx.controller` 5).
- **Teardown always runs and never fails a case.** It finds the run's copies
  by name and marker (so a copy made before an interrupt is found too),
  deletes workflows first, retries while AWX still counts a cancelled job as
  running, and reports what it left as a warning; the exit code stands.
  `--no-cancel` needs `--keep`. `test prune` finds leftovers of any run by the
  same name pattern plus marker (not labels, which apply never creates),
  through the normal preview and confirmation.
- Provisioning needs no confirmation, like the launches a run makes; its cost
  shows in `validate --source-ref` and `run --dry-run`. Stdout of `test run`
  stays `awx.test_result` rows, which `--compare` reads; the copies are
  reported on stderr.

## Related decisions

- Builds on: [AWX specs are plain export documents in the repo](dec_01a0eb415a9e7195873ec9f8a91da3dd-v4-awx-specs-are-plain-export-documents-in-the-repo.md)
- Builds on: [awx test workflow suites reuse the job case](dec_01a0eebbe4e37457a4975c6fd4c3bfb8-v4-awx-workflow-suites-reuse-the-job-case.md)
