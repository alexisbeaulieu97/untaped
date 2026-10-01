# Architectural decisions

These short records preserve constraints and rationale. Code and tests define
implementation behavior. This index is navigation only; work is tracked in GitHub.

- [AWX specs are plain export documents in the repo](awx-specs-are-plain-export-documents-in-the-repo.md)
- [awx test failures name the responsible system; logs are read through events](awx-test-failures-name-the-responsible-system.md)
- [awx test regression tools: declarative checks; a baseline excuses only the same failure](awx-test-regressions-fail-only-on-a-regression.md)
- [awx test temporary test sets: repo specs are copied per run, pinned, and always torn down](awx-temporary-test-sets-are-provisioned-per-run.md)
- [awx test workflow suites reuse the job case; a workflow is blamed on its failing node](awx-workflow-suites-reuse-the-job-case.md)
- [capability state lives in state.yml](capability-state-lives-in-state-yml.md)
- [capability-owned packaged skills](capability-owned-packaged-skills.md)
- [failures carry category and system; exit codes 4/5; JSON diagnostics](failures-carry-category-and-system.md)
- [output and HTTP retry safety](output-and-http-retry-safety.md)
- [packaged skills are self-contained manuals with drift tests](packaged-skills-are-self-contained-manuals-with-drift-tests.md)
- [pipe envelope remains a stable v1 wire contract](pipe-envelope-remains-a-stable-v1-wire-contract.md)
- [root-owned profiles and themes](root-owned-profiles-and-themes.md)
- [stability policy: documented contracts, experimental surfaces, batched majors](stability-policy-and-batched-majors.md)
- [unified application and capability composition](unified-application-and-capability-composition.md)
- [unified distribution and CLI version identity](unified-distribution-and-cli-version-identity.md)
