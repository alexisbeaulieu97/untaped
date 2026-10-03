# Resource documents: `export` and `apply`

`export` writes resources as portable YAML documents, and `untaped awx apply`
creates or updates resources from them. Together they copy configuration
between controllers and profiles, and keep it in a repository.

- [The document](#the-document)
- [Export](#export)
- [Apply](#apply)
- [Workflow templates and their nodes](#workflow-templates-and-their-nodes)
- Applying from a git ref: [source-ref.md](source-ref.md#apply-from-a-git-ref)
- [Copy a template's configuration](#copy-a-templates-configuration)

## The document

```yaml
kind: JobTemplate
apiVersion: untaped.dev/awx/v1
metadata:
  name: Deploy
  organization: Default
spec:
  playbook: deploy.yml
  project: playbooks
  inventory: Production
  credentials: [ssh, vault]
  labels: [web]
  extra_vars: '{"region": "eu"}'
  ask_scm_branch_on_launch: true
  ask_limit_on_launch: true
  survey_enabled: true
  survey_spec:
    name: Deploy survey
    description: ""
    spec:
      - {variable: region, question_name: Region, type: multiplechoice,
         choices: [eu, us], default: eu, required: true}
```

- `kind` is one of `JobTemplate`, `WorkflowJobTemplate`, `Project`,
  `Inventory`, `InventorySource`, `Host`, `Group` and `Schedule`.
  `apiVersion` is `untaped.dev/awx/v1` (optional when applying).
- `metadata` is the identity: `name`, plus `organization` for
  organization-scoped kinds (templates, projects, inventories).
- Hosts, groups and inventory sources name their inventory as
  `parent: {kind: Inventory, name: Production, organization: Default}`.
- A schedule names what it runs as
  `parent: {kind: JobTemplate, name: Deploy, organization: Default}` (or a
  workflow template, project or inventory source).
- `spec` holds the settings with AWX's field names (`untaped awx
  job-templates get NAME --format yaml` shows them); ids, timestamps,
  `status` and `last_job_*` are left out; so are fields the controller
  derives (`custom_virtualenv`, `webhook_key`, and `local_path` unless the
  project is manual) and `organization`, which lives in `metadata`.
  Multi-line text such as `extra_vars` is written as a `|` block, as the UI
  shows it.
- References (organization, project, inventory, credentials, labels,
  instance groups, execution environment) travel by name. They must exist
  where the document is applied, or be created by the same `apply`.
- A workflow template also holds its node graph under `nodes`
  (see [Workflow templates and their nodes](#workflow-templates-and-their-nodes)).
- Unknown fields are sent with a warning.

## Export

```bash
untaped awx inventory-sources export Cloud --inventory Production \
  --inventory-organization Default --out source.yml
untaped awx export --all-kinds --organization Default --out-dir backup
```

- A group's `export` writes its selection as one multi-document YAML stream
  to stdout or `--out FILE`. `untaped awx export --out-dir DIR` writes one
  file per resource and prints the same stream, which pipes into `apply -`.
- `--comment TEXT` writes `# TEXT` at the top of each document (YAML only).
- Credentials are never exported.
- An export of an org-less record writes `metadata.organization: null`, so
  applying it never lands in the default organization.

What a document cannot carry:

- **Secrets.** A job template's non-empty `host_config_key` and the
  non-empty default of every `password` survey question are written as
  `$encrypted$`, which keeps the stored values when applied to the resource
  they came from; empty ones stay empty. A new template drops these
  placeholders with a warning: set a new callback key afterwards. Other survey
  defaults are exported as they are.
- **Access and history.** Roles, permissions, notification attachments,
  schedules of a template (a separate `Schedule` document) and past jobs.
- **Nodes whose template was deleted.** Such a workflow node runs nothing
  untaped can name: the export leaves it out with a warning, and apply leaves
  it alone.

### Export a project's footprint

There is no "with dependents" export; select each kind instead. `apply` orders
documents itself, so the files can go in one directory.

```bash
untaped awx export --kind projects --filter name=playbooks --out-dir rec
untaped awx export --kind job-templates --filter project__name=playbooks --out-dir rec
untaped awx schedules export --parent Deploy --all --out rec/schedules.yml
untaped awx job-templates usage Deploy
untaped awx workflow-templates export Release --out rec/workflows.yml
```

`usage` lists the workflows that run a template; export the ones you want
kept. Inventories and credentials stay references (see the References bullet
above): they must exist where the documents are applied.

## Apply

```bash
untaped awx apply deploy.yml --dry-run
untaped awx apply ./awx-specs --yes
untaped awx apply ./awx-specs --check
untaped awx apply .untaped/awx/templates .untaped/awx/workflows --dry-run
untaped --profile staging awx export --kind job-templates --out-dir exported \
  | untaped --profile prod awx apply - --yes
```

- `apply` creates what is missing and updates what differs, every kind at
  once, in dependency order, after one preview and confirmation.
- A directory contributes every `*.yml`/`*.yaml` in it, so keep other YAML
  out of it.
- `--check` plans without writing: exit 3 when anything would change, 0 when
  nothing would (rows show `planned` or `unchanged`).
- An organization-scoped document without `metadata.organization` is
  scoped by `awx.default_organization`; without a default, a name in several
  organizations is an error, not a guess.
- A `spec.organization` name is used as the identity when metadata omits
  one; `metadata.organization: null` means the org-less record.
- Relationship lists (`credentials`, `labels`, job template and inventory
  `instance_groups`, group `hosts`/`children`, and the same lists on workflow
  nodes) are replaced by adding new members before removing old ones.
- Only a credential sharing a type with an incoming one is removed first
  (AWX allows one per type); a failed add re-adds it and reports `partial`.
- Instance groups are global names, and their order (the fallback order) is
  kept.
- Labels are resolved by name in the template's organization. An unknown
  label fails the apply before any write: apply never creates labels. AWX
  deletes a label once nothing uses it, so removing its last use deletes it.
- `survey_spec: {}` removes a survey. Surveys are written through the
  template's `survey_spec/` endpoint.
- A file that cannot be read or parsed, an unknown kind, or stdin with no
  documents fails the apply, naming the file (`<stdin>` for `apply -`).
- Inventory documents: `apply` refuses only incompatible
  source/configuration combinations. Changing an inventory's settings does
  not rewrite source-managed hosts or groups.
- A constructed inventory and its generated source share `source_vars`,
  `update_cache_timeout`, `limit` and `verbosity`; a batch cannot give them
  different values through both.

## Workflow templates and their nodes

A `WorkflowJobTemplate` document holds its node graph under `spec.nodes`, by
name:

```yaml
kind: WorkflowJobTemplate
apiVersion: untaped.dev/awx/v1
metadata: {name: Release, organization: Default}
spec:
  description: Build, deploy, verify
  inventory: Production
  nodes:
    - id: approve-prod
      approval: {name: Approve production, timeout: 3600}
      success: [deploy]
    - id: deploy
      run: {job_template: Deploy}
      prompts: {limit: "web*", extra_vars: {version: 3}, credentials: [ssh]}
      success: [verify]
      failure: [rollback]
    - id: rollback
      run: {job_template: Shared rollback, organization: Ops}
    - id: verify
      run: {job_template: Smoke check}
      all_parents_must_converge: true
```

| Field | Meaning |
|---|---|
| `id` | Required. The node's key within the workflow (AWX's node `identifier`). Apply matches nodes by it, so keep it stable. |
| `run` | What the node runs: exactly one of `job_template`, `workflow_job_template`, `project` (a project update), `inventory_source` (an inventory update) or `system_job_template` (a management job), by name. |
| `organization` | In `run`: the template's organization when it differs from the workflow's; `null` for a template without one. Management jobs take none. |
| `inventory` | In `run`, with `inventory_source` only (and required there): the inventory holding the source; `organization` is then that inventory's. In `prompts`: the inventory to run against. |
| `approval` | Instead of `run`: an approval step. |
| `name`, `description`, `timeout` | In `approval`: what approvers see, and seconds before it times out (`0`, the default, waits forever). |
| `prompts` | Launch values the node passes to what it runs (not allowed on approvals); AWX accepts one only when the template prompts for it on launch. |
| `success`, `failure`, `always` | Ids of the nodes that run next when this node succeeds, fails, or either. |
| `all_parents_must_converge` | Run only once every parent has reached this node; default `false`. |

`prompts` fields:

| Field | Meaning |
|---|---|
| `inventory`, `credentials`, `labels` | Names in the workflow's organization, or `{name, organization}` for one elsewhere (an export writes that form for you). |
| `instance_groups`, `execution_environment` | Global names; instance groups in fallback order. |
| `extra_vars` | A mapping (AWX's node `extra_data`). |
| `limit`, `scm_branch`, `job_tags`, `skip_tags` | Strings. |
| `job_type` | `run` or `check`. |
| `verbosity` | 0 to 5. |
| `diff_mode` | `true` or `false`. |
| `forks`, `job_slice_count`, `timeout` | Numbers (`timeout` in seconds). |

Export:

- writes the graph from the roots down, nodes that are ready together in `id`
  order, so a replaced node keeps its place in a git diff; edge lists are
  sorted and defaults left out;
- leaves out, with a warning, a node whose template was deleted, and the edges
  into it.

Apply reconciles the graph by `id`, after the workflow itself is written:

1. creates missing nodes (an approval through AWX's
   `create_approval_template`);
2. patches changed nodes (an approval's name, description or timeout on its
   approval template; credentials, labels and instance groups by the
   relationship rules above);
3. deletes nodes no longer listed, except those it cannot name;
4. removes, then adds, edges.

- A node that turns into an approval, or back, is deleted and created again,
  and its edges are re-added.
- `nodes: []` deletes every node untaped can name; a document without `nodes`
  leaves the graph alone.
- Survey-password `extra_vars` read back as `$encrypted$`. A placeholder, in
  the document or in AWX, matches any value, so re-applying an export changes
  nothing and a document holding the plain password converges too.
- A new node cannot take the placeholder: it is created without that
  variable, with a warning.
- The templates the nodes run are ordering dependencies: a job template created
  by the same `apply` is created before the workflow that runs it, and a
  workflow whose template fails is skipped.

The preview shows one row per change, by name. Edges that go away with a
deleted node, or come back with a replaced one, are not listed:

```text
WorkflowJobTemplate/Release id=100 scope=org=Default: planned
  nodes[notify]: null → {"run":{"job_template":"Notify"}} (create)
  nodes[deploy].prompts.limit: "web*" → "web1"
  nodes[approve-prod].approval: {"name":"Approve production","timeout":3600} → {"name":"Approve production","timeout":600}
  nodes[rollback]: {"run":{"job_template":"Rollback"}} → null (delete)
  nodes[verify].always: [] → ["notify"]
```

Refused before any write:

- a duplicate `id`, an edge to an unknown `id`, the same pair of nodes linked
  twice, or a cycle (`nodes form a cycle: a → b → a`);
- two workflows created by the same `apply` that run each other
  (`workflow nodes form a recursion: A → B → A`);
- a template name that does not exist (with a "did you mean" hint).

A node write that fails after the workflow itself was written leaves a
`partial` row naming the node and step (`nodes[deploy] update: …`).
Re-running the same apply picks up from what AWX then holds and finishes the
graph. A graph that does not read back as declared fails with `workflow
nodes did not converge: nodes[deploy].prompts.limit`.

## Copy a template's configuration

Export, change `metadata.name`, apply: the new template gets the same
non-secret configuration (credentials, labels, instance groups, extra vars,
survey, and a workflow's nodes). Use this to copy to another controller;
on the same controller, `copy` does it server-side in one step
([resources.md](resources.md#copy-and-rename-templates)).
