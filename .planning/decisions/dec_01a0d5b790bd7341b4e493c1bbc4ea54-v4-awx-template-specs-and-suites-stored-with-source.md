# AWX template specs and test suites stored with source

Decision ID: `dec_01a0d5b790bd7341b4e493c1bbc4ea54`

Status: **proposed** (design only; nothing here is implemented).

A job template's portable spec (the `export` document, which round-trips
since the template export/apply work) and the `awx test` suites that exercise
it are stored in the playbook's own repository. For any ref of that
repository, `untaped` can rebuild the template with `apply` and run the
suites against it, including branches and commits that precede any release.

Rationale: today the template lives only in AWX and the suites live wherever
someone saved them. The template, its playbook and its tests change together
but are versioned apart, so an old release cannot be rebuilt exactly and a
feature branch cannot be tested with the template configuration it needs.
Storing all three in the same repository puts them under one ref.

## File layout

Specs and suites sit under one root in the playbook repository, by default
`.untaped/awx/` (configurable per repository, see open questions):

```text
<repo>/
├── playbooks/deploy.yml
└── .untaped/awx/
    ├── templates/
    │   └── deploy.yml          # kind: JobTemplate (export format + header)
    └── tests/
        └── deploy-smoke.yml    # kind: AwxTestSuite
```

- One document per file, in today's `export` format, so `apply DIRECTORY`
  already accepts `templates/`.
- `spec.playbook` stays relative to the project root, as AWX expects.
  `spec.project` names the AWX project that points at this repository.
- A repository may hold several templates; each file is one template.

## How a suite refers to its template

`jobTemplate: NAME` (a name resolved in AWX) keeps working unchanged. A new
`template:` key accepts one of:

- `template: {file: ../templates/deploy.yml}`: the spec in the same
  repository, read at the same ref as the suite. The suite then tests the
  template *as rendered for that ref*, not whatever AWX holds now.
- `template: {name: Deploy, organization: Default}`: an existing AWX
  template, the explicit form of today's `jobTemplate`.

`jobTemplate` and `template` are mutually exclusive.

## How a spec is rendered for a ref

Spec files reuse the suite format's optional `---` header and Jinja2 body,
so one loader serves both. Rendering gets a reserved, read-only `source`
context that the header cannot declare:

| Variable | Value |
|---|---|
| `source.repo` | `owner/name` of the repository |
| `source.ref` | the ref as requested (`main`, `v1.4.0`, `feature/x`) |
| `source.sha` | the commit that ref resolved to when the run started |
| `source.ref_kind` | `heads`, `tags` or `commit` |

```yaml
kind: JobTemplate
metadata:
  name: "Deploy{% if source.ref_kind != 'tags' %} [{{ source.ref }}]{% endif %}"
  organization: Default
spec:
  project: acme-playbooks
  playbook: playbooks/deploy.yml
  scm_branch: "{{ source.sha }}"
  inventory: Production
```

- The ref is resolved to a SHA once per run (through `untaped.git`), and
  files are read from that commit with `git show SHA:PATH`, never from the
  working tree. Rebuilding a past version therefore needs no checkout and
  cannot pick up local edits.
- `scm_branch` only takes effect when the AWX project allows the override
  (`allow_override`), the same rule `job-templates list --with-scm` uses for
  `effective_scm_ref`. Rendering fails before any write when the target
  project does not allow it.
- Rebuilding a version is `apply` on rendered files, for example
  `untaped awx apply --source-ref v1.4.0 .untaped/awx/templates/`. Rendering
  is a new, explicit input mode of `apply`; plain `apply FILE` is unchanged.

## Testing a ref before any release

`untaped awx test run .untaped/awx/tests/ --source-ref feature/x`:

1. Resolve `feature/x` to a SHA and read suites and specs at that SHA.
2. For each suite with `template: {file: ...}`, render the spec and apply it
   under an **ephemeral name** (for example `Deploy [test feature/x 1a2b3c4]`).
   Refuse when that name already exists, as `copy` does.
3. Launch the cases against the ephemeral template, then delete it unless
   `--keep` is set. A teardown failure is reported but does not change the
   case results.

A suite that refers to a template by name has no spec to render. With
`--source-ref`, it would launch that template with a launch-time
`scm_branch` override, which works only when the template prompts for it
(`ask_scm_branch_on_launch`). Whether to allow that fallback is an open question.

## Required changes to `awx test`

- Suite model: optional `template:` (file or name), mutually exclusive with
  `jobTemplate`.
- Loader: a filesystem port backed by `git show` at a fixed SHA, beside the
  current working-tree reader; `source` context injection for specs.
- Runner: a provision → run → teardown lifecycle around the existing case
  runner, with a preview and the same confirmation rules as other writes
  (`--yes`, `--dry-run`). Case rows gain `source_ref` and `source_sha`.
- CLI: `--source-ref REF` and `--source-repo PATH` (default: the repository
  containing the suite files), `--keep`, and `test list/validate` accepting
  the same flags so a ref can be checked without launching anything.

## Migration from the current suite format

- Existing suites (`jobTemplate: NAME`, stored anywhere) keep working with
  no change and without `--source-ref`.
- Moving a template into its repository: `job-templates export NAME --out
  .untaped/awx/templates/NAME.yml`, replace the fixed `scm_branch` (and the
  name, when versions must coexist) with `source` expressions, then switch
  the suite to `template: {file: ...}`.
- `test validate` warns when a suite in a repository that holds template
  specs still refers to a template by name, and names the spec file it
  likely matches.

## Constraints

- Everything stays generic to AWX/AAP, Ansible and Git. No host, project or
  organization names are built in.
- Secrets never live in the repository. Password survey defaults and
  `webhook_key` export as `$encrypted$`; ephemeral templates are created
  without them, as a new template from an export is today.
- Git access goes through `untaped.git`; the awx capability does not import
  another capability's code.

## Open questions

- Is `.untaped/awx/` the right default root, and should its location be a
  per-repository file (for example `.untaped/config.yml`) or a capability
  setting?
- Ephemeral names: what fixed pattern avoids collisions across concurrent
  runs of the same ref, and must ephemeral templates carry a label so
  leftovers can be found and pruned?
- Should rebuilding a released version create a separate template per
  version (name derived from `source.ref`), or update one template in place?
- Should `--source-ref` with a by-name suite fall back to a launch-time
  `scm_branch` override, or be refused so a run always tests the stored spec?
- Which AWX project runs the ephemeral template: always the spec's
  `project`, or a dedicated test project with `allow_override`, overridable
  per run?
- Do suites need an explicit format version (`apiVersion`) before the
  `template:` key is added, or is the key's presence enough?
- How are credentials and inventories that differ between test and
  production supplied: header variables, `--vars-file`, or per-environment
  overlay files beside the spec?
- Should workflow templates be in scope, given that their node graphs do not
  round-trip yet?
