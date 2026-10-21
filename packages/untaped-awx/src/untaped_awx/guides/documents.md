---
description: "Read before writing, exporting or applying AWX resource documents: the document format, what an export can't carry, how apply resolves, replaces and orders, and copying a template's configuration."
---

`export` writes resources as portable YAML documents, and `untaped awx apply`
creates or updates resources from them. Together they copy configuration
between controllers and profiles, and keep it in a repository. Applying from a
git ref: `untaped agents awx source-ref`.

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
  (`untaped agents awx workflow-nodes`).
- Unknown fields are sent with a warning.

## Export

```console
$ untaped awx inventory-sources export Cloud --inventory Production --inventory-organization Default --out source.yml
$ untaped awx export --all-kinds --organization Default --out-dir backup
```

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

```console
$ untaped awx export --kind projects --filter name=playbooks --out-dir rec
$ untaped awx export --kind job-templates --filter project__name=playbooks --out-dir rec
$ untaped awx schedules export --parent Deploy --all --out rec/schedules.yml
$ untaped awx job-templates usage Deploy
$ untaped awx workflow-templates export Release --out rec/workflows.yml
```

`usage` lists the workflows that run a template; export the ones you want
kept. Inventories and credentials stay references: they must exist where the
documents are applied.

## Apply

How the `apply` command reads files and confirms:
`untaped agents awx apply`. What it does with each document:

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
- Inventory documents: `apply` refuses only incompatible
  source/configuration combinations. Changing an inventory's settings does
  not rewrite source-managed hosts or groups.
- A constructed inventory and its generated source share `source_vars`,
  `update_cache_timeout`, `limit` and `verbosity`; a batch cannot give them
  different values through both.

## Schedule survey answers

A schedule's survey password answers read back as `$encrypted$` in
`extra_data`:

- applying them back keeps the stored answers;
- changing another `extra_data` key beside one (removing an answer included)
  is refused;
- a new schedule drops them with a warning;
- a real answer typed into `extra_data` is not recognised as secret and shows
  in previews.

## Copy a template's configuration

Export, change `metadata.name`, apply: the new template gets the same
non-secret configuration (credentials, labels, instance groups, extra vars,
survey, and a workflow's nodes). Use this to copy to another controller;
on the same controller, `copy` does it server-side in one step
(`untaped agents awx job-templates copy`).
