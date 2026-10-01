# AWX specs are plain export documents in the repo

Decision ID: `dec_01a0eb415a9e7195873ec9f8a91da3dd`

A playbook repository keeps its AWX configuration beside its tests, as the
documents `export` writes and `apply` reads, with no templating inside them:

```text
<playbook repo>/.untaped/awx/
├── templates/deploy.yml      # kind: JobTemplate
├── workflows/release.yml     # kind: WorkflowJobTemplate (with spec.nodes)
└── tests/deploy-smoke.yml    # kind: AwxTestSuite
```

Folder names are a convention; each document's `kind` decides what it is.

Rationale: `export` → commit → `apply` must stay a pure round trip. Jinja in
specs (a `source.*` template context) would need a second
loader, a render step before every apply, and a new concept to learn, and an
exported file would no longer be the file that is applied. Everything specific
to one test run (a temporary name, the pinned SHA, links between temporary
copies) is applied by the harness, never written in the file.

Constraints:

- Specs are read at a commit (`git ls-tree`, `git show SHA:PATH`), never from
  the working tree, through one reusable git-backed reader. `HEAD` must be
  pushed, as for `--scm-branch HEAD`; a run that makes AWX check the commit out
  also requires that a remote has it. `apply --source-ref REF PATH…` is that
  reader applied to real names; it reads locally, so it keeps the HEAD rule
  only.
- Everything is bound by name, never by ID: references resolve where the file
  is applied, and a workflow graph is keyed by node `id` (AWX's node
  `identifier`) so apply can reconcile it node by node.
- Workflow graphs round-trip (nodes, prompts, approvals, edges), so the workflow
  kind has full fidelity; a node whose template was deleted cannot be named, so
  export skips it with a warning and apply leaves it alone. Node references are
  apply-ordering edges.
- Temporary test sets bind a suite to a repo spec by name and organization,
  provision copies named `NAME [untaped-test SHA RUN]` with `scm_branch` set to
  the SHA, and always tear them down. A template that is neither provisioned
  nor prompting for `scm_branch` is refused under `--source-ref`.
- Secrets never live in the repository; notification attachments, schedules
  and webhook settings are not part of temporary copies.
