# Resource documents: `export` and `apply`

`export` writes resources as portable YAML documents, and `untaped awx apply`
creates or updates resources from them. Together they copy configuration
between controllers and profiles, and keep it in a repository.

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
  organization-scoped kinds (templates, projects, inventories). Hosts, groups
  and inventory sources name their inventory as
  `parent: {kind: Inventory, name: Production, organization: Default}`; a
  schedule names what it runs as
  `parent: {kind: JobTemplate, name: Deploy, organization: Default}` (or a
  workflow template, project or inventory source).
- `spec` holds the settings with AWX's field names (`untaped awx
  job-templates get NAME --format yaml` shows them). References travel by
  name (organization, project, inventory, credentials, labels, execution
  environment); ids, timestamps, `status` and `last_job_*` are left out.
- Unknown fields are sent with a warning.

## Export

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
untaped awx inventories export Production --organization Default --out inventory.yml
untaped awx inventory-sources export Cloud --inventory Production \
  --inventory-organization Default --out source.yml
untaped awx export --kind job-templates --organization Default --out-dir exported
untaped awx export --all-kinds --out-dir backup
```

- A group's `export` writes its selection as one multi-document YAML stream
  to stdout, or to `--out FILE`.
- `untaped awx export --out-dir DIR` writes one file per resource named by its
  full identity, and prints the same documents on stdout (`--print-paths`
  prints the file names instead). `--kind` limits it to one kind,
  `--all-kinds` exports every exportable kind (credentials are skipped).
- An export of an org-less record writes `metadata.organization: null`, so
  applying it never lands in the default organization.

What a document cannot carry:

- **Secrets.** `webhook_key` and the default of every `password` survey
  question are written as `$encrypted$`. Applied to the resource they came
  from, they keep the stored values. A new template drops the password
  defaults with a warning, and a `webhook_key` placeholder refuses the create.
  Other survey defaults are exported as they are.
- **Access and history.** Roles, permissions, notification attachments,
  schedules of a template (a separate `Schedule` document) and past jobs.
- **Workflow graphs.** A `WorkflowJobTemplate` document has no nodes or edges
  (the export says so in a comment and a warning).

## Apply

```bash
untaped awx apply deploy.yml --dry-run
untaped awx apply ./awx-specs --yes
untaped awx apply ./awx-specs --check
untaped --profile staging awx export --kind job-templates --out-dir exported \
  | untaped --profile prod awx apply - --yes
```

- `apply FILE|DIRECTORY|-` reads complete documents (a directory contributes
  every `*.yml`/`*.yaml`, so keep other YAML out of it; `-` reads stdin). It
  creates what is missing and updates what differs, for every kind at once,
  in dependency order, after one preview and confirmation.
- `--check` plans without writing: exit 3 when anything would change, 0 when
  nothing would (rows show `planned` or `unchanged`).
- A document without `metadata.organization` of an organization-scoped kind
  is scoped by `awx.default_organization`; without a default, a name that
  exists in several organizations is an error, not a guess. A
  `spec.organization` name is used as the identity when metadata omits one,
  and `metadata.organization: null` means the org-less record.
- Relationship lists (`credentials`, `labels`, group `hosts`/`children`,
  inventory `instance_groups`) are replaced by adding new members before
  removing old ones; only a credential sharing a type with an incoming one is
  removed first (AWX allows one per type), and a failed add re-adds it and
  reports `partial`.
- Labels are resolved by name in the template's organization. An unknown
  label fails the apply before any write: apply never creates labels.
- `survey_spec: {}` removes a survey. Surveys are written through the
  template's `survey_spec/` endpoint.
- A file that cannot be read or parsed, an unknown kind, or stdin with no
  documents fails the apply, naming the file (`<stdin>` for `apply -`).
- Inventory documents: `apply` accepts every representable inventory and
  refuses only incompatible source/configuration combinations. Changing an
  inventory's settings does not rewrite source-managed hosts or groups. A
  constructed inventory and its generated source share `source_vars`,
  `update_cache_timeout`, `limit` and `verbosity`; a batch cannot give them
  different values through both.

## Copy a template's configuration

Export, change `metadata.name`, apply: the new template gets the same
non-secret configuration (credentials, labels, extra vars, survey).

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
untaped awx apply deploy.yml --yes
```

Edit `metadata.name` between the two commands. `untaped awx job-templates
copy` does the same server-side in one step (see
[resources.md](resources.md#copy-and-rename-templates)).
