# AWX / AAP

`untaped awx` talks to **Ansible Automation Platform** (AAP) and
**upstream AWX** through their REST API. It's built around two
workflows:

- **Inspect and operate** — list, get, launch jobs, watch them.
- **Configure as code** — `save` a resource to a YAML file, edit it,
  `apply` the file (preview by default; pass `--yes` to actually
  write). Works as a backup/restore tool and as a way to keep AWX
  configuration under git.

Plus a small testing surface (`awx test`) that runs declarative,
parameterised launch matrices against a job template.

## Setup

```bash
untaped config set awx.base_url https://aap.example.com
untaped config set awx.token <bearer-token>
# Upstream AWX users only — AAP defaults to /api/controller/v2/
untaped config set awx.api_prefix /api/v2/

# Optional: name disambiguation for get/launch/update/save/apply when
# the same name exists in multiple orgs. Does NOT scope `list` — use
# `--filter organization__name=<org>` for that.
untaped config set awx.default_organization Engineering

# Health check
untaped awx ping
```

The token is treated as a secret (`SecretStr`): `untaped config list`
redacts it as `***` unless you pass `--show-secrets`. See
[`configuration.md`](./configuration.md) for the full schema, profiles,
and TLS knobs (corporate CAs work out of the box via the OS trust
store).

## Resources, kinds, fidelity

`untaped` exposes one sub-app per AWX resource kind. What's CRUDable
versus read-only depends on a per-kind **fidelity** tier:

| Sub-app              | Kind                  | Fidelity  | save | apply | notes                                                       |
| -------------------- | --------------------- | --------- | ---- | ----- | ----------------------------------------------------------- |
| `job-templates`      | `JobTemplate`         | full      | yes  | yes   | also supports `launch`                                      |
| `projects`           | `Project`             | full      | yes  | yes   | also supports `update` (SCM sync)                           |
| `schedules`          | `Schedule`            | full      | yes  | yes   | parent (JT or workflow) must exist                          |
| `workflow-templates` | `WorkflowJobTemplate` | partial   | yes  | yes   | also `launch`; node graph + edges not roundtripped          |
| `credentials`        | `Credential`          | read_only | no   | no    | list / get only — `$encrypted$` roundtrip deferred to v0.5  |
| `organizations`      | `Organization`        | read_only | no   | no    | list / get only                                             |
| `inventories`        | `Inventory`           | read_only | no   | no    | list / get only                                             |
| `credential-types`   | `CredentialType`      | read_only | no   | no    | list / get only                                             |

Read-only kinds are still useful for FK resolution: when you `apply` a
JobTemplate that references `Engineering` as its organization,
`untaped` looks the name up against `organizations` to get the id.

Saves below `full` echo the fidelity tier to stderr and embed an
inline YAML comment so the loss is visible.

## Per-resource commands

Every CRUDable kind has the same shape; replace `<kind>` with one of
the sub-apps above.

```bash
untaped awx <kind> list [--search <q>] [--filter KEY=VALUE]... [--limit N]
                        [--stdin] [--with-names]
                        [--format json|yaml|table|raw] [--columns ...]

untaped awx <kind> get <name>... [--stdin] [--organization <org>]
                                 [--with-names]
                                 [--format yaml|json|table|raw] [--columns ...]

untaped awx <kind> save <name> [--out FILE] [--organization <org>]

untaped awx <kind> apply --file FILE [--yes] [--fail-fast]
                         [--format json|yaml|table|raw] [--columns ...]
```

`--filter` is repeatable and passed verbatim to AWX's REST API, so any
Django-style lookup it supports works without code changes:

```bash
untaped awx job-templates list --filter organization__name=Engineering
untaped awx job-templates list --filter name__icontains=deploy \
                               --filter playbook__contains=deploy.yml
untaped awx projects list --filter scm_type=git --filter status=successful
```

`--with-names` flips FK columns from numeric ids to the names AWX
returns under `summary_fields`. Multi-valued FKs (e.g. `credentials`)
become lists of names. Skip the flag (the default) to keep raw ids,
which is what the FK-piping shape relies on:

```bash
# Human-readable list — names instead of ids
untaped awx job-templates list --with-names

# Pipe-friendly: ids feed the next `get --stdin`
untaped awx job-templates list --columns project --format raw \
  | sort -u \
  | untaped awx projects get --stdin --columns name
```

`--stdin` flips `list` into a consumer: it reads newline-separated
names or numeric ids from stdin and renders only those records using
the tabular columns view. Same identifier semantics as `get --stdin`
(digits → id, otherwise name), but the output is `list`'s columns
table rather than `get`'s per-record yaml. Cannot be combined with
`--search`, `--filter`, or `--limit` — those are server-side filtering
knobs and identifier lookup is a different mode.

```bash
# Curated tabular view across a known set of templates
echo -e "deploy-web\ndeploy-api" \
  | untaped awx job-templates list --stdin --with-names \
                                   --columns name --columns project
```

### Sub-endpoint membership: `<parent> <sub_endpoint> add/remove`

Any kind whose spec declares an `FkRef(multi=True, sub_endpoint=…)`
automatically gets a nested `add` / `remove` sub-app for that edge.
Today that's `groups hosts` and `groups children`. The verbs are
additive — they don't read existing members first, just POST
associate/disassociate into AWX (which returns 204 on re-add or
re-remove, so they're safe to run repeatedly).

```bash
# Add hosts directly
untaped awx groups hosts add prod-web host-01 host-02

# Pipe-friendly: feed a filtered host set into a group
untaped awx hosts list --filter inventory__name=prod \
                       --columns name --format raw \
  | untaped awx groups hosts add prod-web --stdin

# Remove the inverse
untaped awx groups hosts remove prod-web host-01
```

For nested fields outside the FK set (e.g. last-job status, polymorphic
schedule parents), use dotted column paths — `format_output` walks
nested dicts:

```bash
untaped awx job-templates list \
  --columns name --columns summary_fields.last_job.status --format table

untaped awx schedules list \
  --columns name --columns summary_fields.unified_job_template.name --format table
```

`save` writes (or prints to stdout) a kubectl-style envelope:

```yaml
kind: JobTemplate
apiVersion: untaped.dev/v1
metadata:
  name: deploy-app
  organization: Engineering
spec:
  description: Deploy the app
  inventory: Web Inventory
  project: ansible-playbooks
  credentials: [aws-prod]
  # ...
```

FK references are by **name** (scoped to `metadata.organization` where
relevant), not by id, so a saved file is portable between AAP
instances that share resource names.

`apply` is **preview by default** — it prints what *would* change and
exits without writing. Pass `--yes` to actually write. The diff is
field-level; declared secret paths (`inputs.*`, `webhook_key`)
carrying `$encrypted$` are stripped from the PATCH and shown as
`(preserved existing secret)` rows.

### `launch` (job templates and workflow templates)

```bash
untaped awx job-templates launch <name>... [--stdin]
    [--extra-vars KEY=VAL]... [--limit <pattern>]
    [--inventory <name>] [--credential <name>]...
    [--scm-branch <b>] [--job-tag <t>]... [--skip-tag <t>]...
    [--verbosity 0..4] [--diff-mode/--no-diff-mode] [--job-type run|check]
    [--wait | --monitor]
    [--organization <org>]
```

Names like `--inventory` and `--credential` resolve to ids using the
same FK lookup the apply pipeline uses. Flags that the kind doesn't
accept (e.g. most launch flags on a workflow template) are rejected
loudly rather than silently dropped.

`--wait` / `--monitor` block until the job reaches a terminal state.

### `update` (projects only)

```bash
untaped awx projects update <name> [--wait]
```

Triggers an SCM sync on the project.

## Top-level commands

### `untaped awx apply` (multi-kind)

```bash
untaped awx apply --file FILE_OR_DIR [--yes] [--fail-fast]
```

Apply a single file or a whole directory of YAML envelopes. When
multiple kinds are present, `untaped` orders them by their declared FK
dependencies so referenced resources exist before referencing ones:

```text
Organization → CredentialType → Credential → Project → Inventory
            → JobTemplate → WorkflowJobTemplate → Schedule
```

Catalog-only kinds (`ExecutionEnvironment`, `Label`, `InstanceGroup`)
exist solely so launch and apply payloads can resolve names to ids;
they have no CLI sub-app and don't take part in the ordering above.

Per-kind `apply` (e.g. `awx job-templates apply`) only writes its own
kind — wrong-kind docs in the file are warned about and never written.
Use the top-level `awx apply` when you want the dependency ordering.

### `untaped awx save --all` (bulk dump)

```bash
untaped awx save --out-dir backup/ --all
untaped awx save --out-dir backup/ --all --filter organization__name=Engineering
untaped awx save --out-dir backup/ --kind JobTemplate
```

Writes one file per resource. Filenames encode the full identity so
same-named records across organizations don't collide:
`<Kind>[__<org>][__<parent_kind>__[<parent_org>__]<parent_name>]__<name>.yml`.
Read-only kinds (Credential, etc.) are skipped with a one-line note.

`--filter KEY=VALUE` (repeatable) is passed verbatim to AWX for every
saved kind. AWX's `/schedules/` endpoint has no `organization` field,
so `--filter organization__name=…` is rejected by that endpoint —
schedules don't get included in an org-scoped backup. Run a separate
`save --kind Schedule` (no filter) if you also need schedules.

### `untaped awx jobs`

Read-only access to execution records — useful after a launch.

All `jobs` subcommands take a common `--kind` discriminator (default
`job`; also accepts `workflow_job`, `project_update`, `inventory_update`,
`ad_hoc_command`) and the standard `--format` / `--columns` knobs.

```bash
# Newest-first list. Default columns: id,name,status.
untaped awx jobs list [--status STATUS] [--filter K=V]... [--limit N]

# Single job record (defaults to YAML).
untaped awx jobs get <id>

# Structured per-task events. Default columns: counter,event,host_name,task.
# --filter reaches AWX server-side (event=runner_on_failed, host=web-01, …).
# --follow polls until the job is terminal; --from-counter N skips early events.
untaped awx jobs events <id> [--follow] [--from-counter N] [--filter K=V]...

# Plain stdout (default --format raw). --follow polls until terminal;
# --tail N keeps the last N historical lines before any follow phase;
# --grep PATTERN is client-side regex (case-insensitive with -i).
untaped awx jobs logs <id> [--follow|-f] [--tail N] [--grep PATTERN] [-i]

# Block until terminal. Exits 1 on --timeout.
untaped awx jobs wait <id> [--timeout SECS]
```

`jobs events --follow` is format-aware: `--format table` (default)
renders human PLAY/TASK output coloured to mirror AWX's UI, while
`--format json` streams NDJSON (one event per line) so you can pipe
into `jq` directly. Other formats stream one structured row per event.

### `untaped awx unified-templates`

Read-only browser over AWX's `/unified_job_templates/` virtual collection,
which interleaves `JobTemplate`, `WorkflowJobTemplate`, `Project`, and
`InventorySource` rows behind a single `type` discriminator.

```bash
# Alphabetical (so the four kinds interleave predictably).
# Default columns: id,name,type — deliberately minimal because health
# fields differ across kinds (JT/WJT use `last_job_status`; Project /
# InventorySource use `status`), so any one column would be empty for
# half the rows. Opt in via --columns.
untaped awx unified-templates list [--type TYPE] [--filter K=V]... [--limit N]

# id-only. Names are not unique across kinds, so this fast-fails on a
# non-decimal identifier with a message pointing at the per-kind
# sub-apps for name lookup.
untaped awx unified-templates get <id> [<id>...] [--stdin]
```

`--type TYPE` is sugar for `--filter type=…`; passing both with
conflicting values is rejected. Launch dispatch is intentionally out of
scope here — use the per-kind sub-apps (`job-templates launch`,
`projects update`, …).

### `untaped awx workflow-templates nodes`

Read-only inspector for a workflow's contents — answers "which jobs
run inside this workflow?". Lives on the `workflow-templates` sub-app
alongside `list`/`get`/`save`/`apply`/`launch`. The node graph itself
(success/failure/always edges) is still out of scope; this surface
shows *what* runs, not the DAG structure.

```bash
# Top-level nodes only. Default columns: id,name,type,depth. Add
# ``--columns id,identifier,name,type,depth`` if you want the DAG label.
untaped awx workflow-templates nodes <name|id> [--organization ORG]

# Flatten sub-workflows. ``depth`` tags each row's distance from the
# root (0 = root's own nodes, 1 = one sub-workflow deep, …).
untaped awx workflow-templates nodes <name|id> --recursive
untaped awx workflow-templates nodes <name|id> --recursive --depth 2

# ``--depth N`` for N>0 implies ``--recursive``; ``--depth 0`` means
# "only the root" (the default when neither flag is passed).
untaped awx workflow-templates nodes <name|id> --depth 1

# Narrow the output to one template-type discriminator. Traversal
# still descends into every workflow node, so ``--type job_template``
# combined with ``--recursive`` shows every job template anywhere in
# the workflow tree.
untaped awx workflow-templates nodes <name|id> --recursive --type job_template
untaped awx workflow-templates nodes <name|id> --type workflow_job_template
```

Numeric identifiers (`nodes 100`) skip the name lookup; names follow
the same org-scope rules as `workflow-templates get`. Recursion is
cycle-guarded by workflow id — a workflow that re-enters itself emits
a `warning: cycle: workflow <id> already visited; skipping` line to
stderr and is skipped on the second visit. Nodes whose referenced
template has been deleted (`unified_job_template: null`) still appear
with `name` and `type` empty.

## Test suites — `untaped awx test`

Declarative, parameterised launch matrices against a job template.
One file = one job template + many input variations + one
pass/fail report. v1 verdict is AWX's `successful` job status; richer
assertions land in v2 (the `assert:` block is reserved).

```bash
untaped awx test list     FILE_OR_DIR...           # cases that would run
untaped awx test validate FILE_OR_DIR...           # render + parse + resolve, no launch
untaped awx test run      FILE_OR_DIR... [--case NAME]...
                                         [--parallel N] [--timeout SECS]
                                         [--show-logs] [--format ...]

# Variable-resolution flags accepted by all three subcommands:
#   [--var k=v]...  [--vars-file PATH]...  [--non-interactive]
```

Exit code is `0` only when every case passes. `--show-logs` (`-v`)
dumps the tail of AWX's stdout to stderr for any failing job.

### File shape

```yaml
---
# YAML frontmatter — variable metadata. NOT Jinja-rendered.
variables:
  env:
    description: Target environment
    type: choice
    choices: [dev, staging, prod]
    default: dev
  api_token:
    description: One-time API token (no-echo prompt)
    type: string
    secret: true
---
# Body — Jinja2-rendered with the resolved variables, then parsed as YAML.
kind: AwxTestSuite
name: deploy-app
jobTemplate: "Deploy app"
defaults:
  launch:
    extra_vars:
      log_level: info
cases:
  smoke:
    launch:
      inventory: "Web Inventory"
      credentials: ["github-pat"]
      labels: ["smoke"]
      extra_vars:
        env: {{ env | to_yaml }}
        api_token: {{ api_token | to_yaml }}
```

See [`../examples/test-deploy-app.yml`](../examples/test-deploy-app.yml)
for a fuller example using `{% for %}` to multiply cases across regions
and the `!ref` escape hatch.

### Variables

Each variable supports `name`, `type` (`string` / `int` / `bool` /
`choice` / `list`), `default`, `choices`, `secret`, and `description`.
Precedence, high to low:

```text
--var k=v   >   --vars-file   >   default   >   interactive prompt
```

`--non-interactive` (or running without a TTY) fails fast on missing
required variables instead of prompting.

### Name resolution and the `!ref` tag

Bare strings on FK fields under `launch:` (`inventory`, `project`,
`credentials`, `organization`, `execution_environment`, `labels`,
`instance_groups`) resolve from name to id automatically.

Resolution is **top-level only on declared FK fields, never
recursive** — `extra_vars` is passed through verbatim. When you need a
name lookup *inside* `extra_vars` (or any other opaque dict), use the
`!ref` tag:

```yaml
extra_vars:
  target_inventory_id: !ref { kind: Inventory, name: "Web Inventory" }
```

Structurally distinct from a regular dict, so user content like
`{name: Alice}` is never misinterpreted.

### Pass-through with typo warnings

Fields under `launch:` match AWX's API verbatim. Anything outside the
v2.x known-fields set and not declared as an FK triggers a stderr
warning ("unknown launch field 'frooks' — typo? passing through to
AWX") and still passes through, so new AWX fields work without a
client update.

## Worked example: copy a job template between AAP instances

```bash
# Save from staging.
untaped --profile staging awx job-templates save "Deploy app" \
  > deploy-app.yml

# Preview against prod (no write).
untaped --profile prod awx job-templates apply --file deploy-app.yml

# Looks right? Apply for real.
untaped --profile prod awx job-templates apply --file deploy-app.yml --yes
```

Or back up and restore in bulk:

```bash
untaped --profile staging awx save --out-dir backup-staging/ --all
untaped --profile prod awx apply --file backup-staging/ --yes
```

Apply ordering ensures Organizations and Credentials land before the
Job Templates that reference them.

## See also

- [`configuration.md`](./configuration.md) — AWX setup keys, profiles,
  TLS for corporate CAs.
- [`workspace.md`](./workspace.md) — keep your AAP YAML envelopes in a
  workspace alongside the playbooks they configure.
- [`packages/untaped-awx/AGENTS.md`](../packages/untaped-awx/AGENTS.md) —
  the resource framework internals (`ResourceSpec`, `ApplyStrategy`,
  `FkResolver`, apply ordering, runner phases).
- [AGENTS.md](../AGENTS.md) — workspace-wide rules and recipes.
