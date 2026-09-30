# AWX/AAP usage

Use `untaped awx ...` for Ansible Automation Platform (AAP) or AWX. The
current command surface is generated from the resource catalog; use
`untaped awx --help` and `untaped awx <resource> --help` for the complete list
of fields and options.

## Connect to AAP

The default AAP gateway prefix is `/api/controller/v2/`. Standalone AWX
usually uses `/api/v2/`. Configure the profile, then check the connection:

```bash
untaped config set awx.base_url https://aap.example.com
printf '%s\n' "$AAP_TOKEN" | untaped config set awx.token --stdin
untaped awx ping
```

`ping` reads the unauthenticated `/ping/` health endpoint and then `/me/`, so
a rejected token fails the command; the output includes the authenticated
`user`.

Use `untaped --profile <name> awx ...` to select a different configured
profile. Tokens are secret settings; do not put them in a manifest or command
history. To keep the token out of `config.yml` too, set `awx.token_command`
to a command that prints it, for example
`untaped config set awx.token_command '["pass", "show", "aap/token"]'`, or
export `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` or `AAP_TOKEN` (the
variables the `ansible.controller` collection reads). `awx.token` wins over
`awx.token_command`, which wins over the variables, tried in that order; see
[Tokens](../configuration.md#tokens).

The writable resource groups are job templates, workflow templates, projects,
schedules, hosts, groups, inventories, and inventory sources. They support the
shared `list`, `get`, `export`, `apply`, `patch`, `edit`, and `delete` lifecycle
where shown by their help. Credentials, credential types, organizations, and
unified templates remain lookup or browse views. `awx jobs` inspects execution
records and does not edit configuration.

## Select resources

Selection modes are exclusive. For selection-based commands, use positional
names, names with `--by-id`, `--stdin`, `--filter`/`--search`, or explicit
`--all`; do not combine modes.
Organization, inventory, inventory-organization, and parent options constrain
both lookup and server filters. Each resource group offers only the scopes it
has: `--organization` for organization-scoped kinds (templates, projects,
inventories, credentials), `--inventory`, `--inventory-organization` and
`--parent` for hosts, groups and inventory sources, and `--parent` for
schedules. Any other scope option is unknown to that group (exit 2).
Selection-based mutation commands require an explicit selection or `--all`.

```bash
untaped awx job-templates list --filter name__icontains=deploy
untaped awx inventory-sources list --inventory Production --inventory-organization Default
untaped awx inventory-sources patch Cloud --inventory Production \
  --inventory-organization Default --set update_cache_timeout=3600
```

`--filter` is repeatable and is passed to AWX using its server-side lookup
syntax. Names that remain ambiguous require a narrower scope or an ID. A name
that is not found names its scope and suggests close names from that scope
(`JobTemplate not found: 'deplyo' in organization 'Default'; did you mean
'deploy'?`); when `awx.default_organization` supplied the organization, a
second line says so and to pass `--organization` to search elsewhere (a
missed `launch --launch-inventory` or `--credential` name too). The
resolver de-duplicates a kind and ID, validates the complete selection before
mutation, and performs no writes for an empty or invalid selection.

`--format pipe` emits typed v1 records containing the kind and ID. A typed
consumer uses those IDs directly instead of resolving names again:

```bash
untaped awx job-templates list --filter name__icontains=deploy --format pipe \
  | untaped awx job-templates patch --stdin --set verbosity=2
```

Bare stdin lines retain the command's normal name or `--by-id` meaning. Do not
mix bare lines and typed envelopes. A typed record of another kind (for
example `awx.host` piped into `projects patch`) exits 2, and empty stdin is an
error (`no identifiers received on stdin`). `jobs * --stdin` accepts `awx.job`
records and launch/sync results. Piped selections still use the controlling
terminal for confirmation when one is available; machine data stays on
stdout, while previews, prompts, progress, and warnings go to stderr.

## Patch shared fields

`patch` changes existing resources only. Repeat `--set KEY=VALUE` or provide a
YAML/JSON mapping with `--patch-file`; `--set` wins when both specify a field.
Values use JSON coercion when possible (`true`, `false`, numbers, arrays,
objects, and `null`), except that a field the selected record holds as a string
stays a string unless the value is a JSON object or array (`scm_branch=1.10`
stays `"1.10"`). A supplied value replaces that top-level field, and an
omitted field is unchanged. Nested objects are not implicitly merged.

An unknown field name that closely matches a known field is rejected as a
likely typo (`verbostiy=2` exits 2 with "did you mean verbosity?" before any
request); pass `--allow-unknown-fields` to send it anyway. Other unknown names
(for example a field a newer AWX added) are sent, and `apply`, `patch`, and
`edit` all warn on stderr when a document carries unknown fields.

```bash
untaped awx inventory-sources patch \
  --filter inventory__name=Production \
  --set update_cache_timeout=3600

untaped awx job-templates patch \
  --filter name__icontains=deploy \
  --patch-file changes.yml \
  --set verbosity=2
```

Inventory cache timeouts are seconds; `0` is a valid value. Changing
`update_cache_timeout` alone does not change `update_on_launch`. The same
replacement rule applies to structured variables: an empty map clears the
map, omitted keys are removed, and ordered lists retain their order.

Foreign-key integers mean controller IDs. Strings mean names in the selected
scope, including numeric-looking strings. Because `--set` JSON-decodes values,
quote the JSON string when the name itself is numeric:

```bash
untaped awx job-templates patch deploy --organization Default \
  --set 'inventory="123"' --dry-run
```

Here `inventory="123"` selects the resource named `123`; unquoted
`inventory=123` means controller ID `123`. The same distinction can be made by
quoting the value in a `--patch-file`. Ambiguous, missing, or out-of-scope
references fail before any write. Mapping references retain that same scope;
explicit organization or inventory ancestry must agree with it. Constructed
input-inventory memberships may span organizations when explicitly identified.
Identity, parent, kind, and read-only fields
cannot be patched: use `apply` for create or declarative create/update, and
`delete` for removal. A patch never renames, reparents, or creates a resource.

Known current and newly entered secret values are redacted from previews,
result changes, and controller errors. Saved specifications use
`$encrypted$` placeholders where the controller does not return a secret.
AWX returns a schedule's survey password answers in `extra_data` as
`$encrypted$`: applying them back keeps the stored answers, a change to
another `extra_data` key beside one (including removing another answer) is
refused, and a new schedule drops them with a warning. untaped cannot tell
which `extra_data` keys are passwords, so a real answer you type there is not
redacted: previews and results show it in plain text.

## Edit different values together

`edit` opens one YAML multi-document batch for the selected resources. Set
`VISUAL` or `EDITOR` to an editor that waits until the file closes, such as
`code --wait`:

```bash
export VISUAL='code --wait'
untaped awx job-templates edit \
  --filter name__icontains=deploy \
  --field inventory --field verbosity
```

Each document has immutable identity metadata and an editable `spec`. A
repeatable `--field` limits the editable top-level fields. Removing a document
deselects it; removing a field leaves that field unchanged. Supplied nested
values replace the entire top-level value. Identity changes, name or parent
changes, new documents, duplicate documents, and retargeting are rejected.
Use `apply` to create resources; `edit` and `patch` cannot create them.

The editor needs a real controlling terminal at `/dev/tty`, even when stdin is
piped or `--yes` is supplied. All three editor streams use that terminal so
editor chatter cannot corrupt machine stdout. The session directory is
private (mode `0700`) and the YAML file is owner-only (mode `0600`). The file
and directory are removed after a clean session; an editor, parse, or
validation failure retains the edited file and prints its path. Invalid YAML
can be reopened or cancelled. A no-op editor session does not prompt or write.

## Apply, export, and inventory lifecycle

`awx apply PATH…` is the declarative create/update path, for every kind at
once. It accepts complete portable YAML documents from one or more files or
directories, resolves dependencies across all of them, previews the full batch
once, and writes only after confirmation. `apply -` reads the documents from
stdin, so an export from one profile can be applied to another without a file:

```bash
untaped awx apply ./awx-specs --dry-run
untaped awx apply ./inventory.yml --yes
untaped --profile staging awx export --kind job-templates --out-dir exported \
  | untaped --profile prod awx apply - --yes
untaped awx apply ./awx-specs --check
untaped awx apply --source-ref v1.4.0 .untaped/awx/templates .untaped/awx/workflows
```

`--check` computes the same plan and writes nothing: it exits 3 when any
document would change the controller (drift) and 0 when everything is
already in place, so CI can tell drift from failure (1). The rows show
`planned` or `unchanged`.

A directory contributes every `*.yml` and `*.yaml` file, so keep other YAML
(for example CI or vars files) out of it; a file that cannot be read or parsed,
or holds an unknown kind, fails the apply with its path (`<stdin>` for
`apply -`) named. Stdin with no documents (empty, or only `---` and comments)
is an error, and so is an unknown option (`apply --chekc`, exit 2).
A document of an
organization-scoped kind without `metadata.organization` is scoped by
`awx.default_organization`, as selection and `awx test` are. With no default
configured, a name that exists in more than one organization is an ambiguity
error rather than a guess. A `spec.organization` name is used as the identity
when metadata omits one, and an explicit `metadata.organization: null` means
the org-less record (for example a global workflow template); `export` writes
that null for org-less records so an export/apply round trip never lands in the
default organization.

Relationship lists (template `credentials`, `labels` and `instance_groups`,
group `hosts`/`children`, inventory `instance_groups`) are replaced by adding
new members before removing old ones, so a refused add never leaves a template
without its credentials. Only a
credential that shares a type with an incoming one is removed first (AWX allows
one per type); if the add then fails, the removed members are re-added and the
row reports `partial`. Instance groups are looked up by name in every
organization, and their order is kept: it is the order AWX falls back through.

`export` writes a fixed selection as portable YAML. Per-resource export accepts
`--out FILE`; without it (or with `--out=-`), YAML is written to stdout. A
symlinked FILE is written through the link, and FIFOs or `/dev/stdout` are
written directly. Inventory and source exports preserve organization and
parent identity:

```bash
untaped awx inventories export Production --organization Default \
  --out inventory.yml
untaped awx inventory-sources export Cloud --inventory Production \
  --inventory-organization Default --out source.yml
```

Constructed inventory settings use the constructed inventory route and its
managed source. Operation support is specific to the workflow: inventory sync
rejects smart or source-less inventories and invalid or manual sources during
preflight, while apply accepts representable inventory documents and rejects
only incompatible source/configuration combinations. Editing inventory
settings does not recreate or rewrite source-managed hosts or groups. A
constructed inventory and its generated source share `source_vars`,
`update_cache_timeout`, `limit`, and `verbosity`. A batch cannot request
conflicting values for those fields through the two resources.

### Template export round trip

A job or workflow template export carries its settings, `extra_vars`,
`credentials`, `labels` and (job templates) `instance_groups` by name, its
survey, and (workflows) its whole node graph. Applying that file under
another `metadata.name` creates a template with the same non-secret
configuration:

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
# edit metadata.name to "Deploy next", then:
untaped awx apply deploy.yml --yes
```

Surveys are read from and written to the template's `survey_spec/` endpoint;
`survey_spec: {}` removes the survey. Labels are resolved by name in the
template's organization. An unknown label fails the apply before any write:
apply never creates labels, so a typo cannot add one silently. AWX deletes a
label once no resource uses it, so removing a template's last use of a label
also deletes the label.

What an export cannot carry:

- **Secrets.** `webhook_key` and the default of every `password` survey
  question are written as `$encrypted$`. Applying the file to the template it
  came from keeps the stored values. Applying it as a new template drops the
  password defaults with a warning, so the new template starts without them;
  a `webhook_key` placeholder refuses the create. Other survey defaults are
  exported as they are.
- **Access and history.** Roles, team and user permissions, notification
  attachments, schedules, and past jobs are not part of the document.
- **Server-managed fields.** IDs, timestamps, `last_job_*` and `status` are
  dropped; references (organization, project, inventory, execution
  environment, credentials, labels, instance groups) travel by name, so they
  must already exist where the file is applied, or be created by the same
  `apply`.

### Workflow templates and their nodes

A workflow template document holds its node graph under `spec.nodes`: each
node has an `id` (AWX's node identifier), what it `run`s (a job template,
workflow, project, inventory source or management job, by name) or an
`approval`, its `prompts` by name, and its `success`/`failure`/`always`
edges. `export` writes the whole graph and `apply` reconciles it node by node,
with the usual preview.

### Apply from a git ref

`apply --source-ref REF PATH...` reads the paths as they are at a git ref of
the current repository, never from the working tree:

```bash
untaped awx apply --source-ref v1.4.0 .untaped/awx/templates .untaped/awx/workflows --dry-run
```

The complete document format (the node fields and prompts, how apply
reconciles a graph, what the preview shows, what is refused) and the
`--source-ref` rules ship with the CLI in the awx skill's
`references/specs.md`
([specs](../../src/untaped/capabilities/awx/skills/untaped-awx/references/specs.md)),
installed with `untaped skills install awx`.

## Copy templates

`copy` asks AWX to copy one job or workflow template under a new name, in the
source's organization:

```bash
untaped awx job-templates copy Deploy --name "Deploy next" --organization Default --dry-run
untaped awx job-templates copy Deploy --name "Deploy next" --format pipe --yes \
  | untaped awx job-templates patch --stdin --set scm_branch=main --dry-run
```

The source is selected like any other single target (name plus scope, or
`--by-id`). Before any write, `copy` refuses a name already used in the
source's scope, the source's own name, and a source AWX reports it cannot
copy (`can_copy: false`). When AWX reports it cannot copy everything without
user input (workflow templates referencing templates, credentials or
inventories the caller cannot use), the preview warns about each part it
will leave behind, and the outcome lists them in `not_carried`. The copy
previews and confirms like other writes, and `--dry-run` never writes.

The `awx.copy_outcome` record (`id`, `name`, `source_id`, `kind`, `action`,
`not_carried`) names the new template. `patch`, `delete`, `launch` and the other
`--stdin` selections of the same kind accept it.

## Rename templates

`patch` never changes a name. `rename` is the separate, explicit command for
job and workflow templates:

```bash
untaped awx job-templates rename Deploy "Deploy app" --organization Default --dry-run
untaped awx job-templates rename Deploy "Deploy app" --format pipe --yes \
  | untaped awx job-templates patch --stdin --set scm_branch=main --dry-run
```

One resource per call, selected like any single target. Before any write,
`rename` refuses a new name already used in the same scope (the same
organization, or no organization for an org-less workflow template) and the
resource's current name. The preview shows the old and new names; it
confirms like other writes, and `--dry-run` never writes. After the write,
the resource is read again: if AWX does not show the new name, the row is
`failed` and the command exits 1.

The `awx.rename_outcome` record (`id`, `name`, `old_name`, `kind`, `action`:
`planned`, `renamed` or `failed`) names the resource by `id`, so `--stdin`
selection of the same kind accepts it.

## Launch templates

`launch` submits job or workflow templates. `--extra-vars` is
repeatable and merged left to right into one mapping sent as JSON; a later
entry wins over an earlier one for the same key:

- `KEY=VAL`: only `true`/`false`/`null`, integers (`count=2`), and JSON
  objects or arrays (`tags=["a"]`) are decoded; everything else is kept as the
  typed string (`region=us-east`, `version=1.10`, `ratio=1.5`, `n=1e3`).
- `@PATH`: a YAML or JSON mapping file (`.json` parses as JSON).
- A raw JSON or YAML mapping: `'{"region": "eu"}'` or `'region: eu'`.

YAML dates and timestamps are sent as ISO strings (`2024-01-01`). Values JSON
cannot carry (`.nan`, `!!binary`) are a usage error.

```bash
untaped awx job-templates launch Deploy --organization Default \
  --extra-vars @vars.yml --extra-vars version=1.10.0 --host-pattern web --wait
untaped awx job-templates launch Deploy --launch-inventory Staging --dry-run -f yaml
```

`--organization` scopes the lookup of the template (and of `--launch-inventory`
and `--credential` names). `--launch-inventory NAME|ID` is the inventory the
job runs against; digits mean an AWX id. `--dry-run` resolves everything and
submits nothing: each `planned` row carries the `payload` the launch would
send, with names resolved to ids and `extra_vars` merged into a mapping
(compact JSON in the `table` and `raw` formats).
Secrets are shown as `<redacted>`: the answers to the template's `password`
survey questions and any variable, at any depth, whose name looks secret.
A name is split into words at `_`, `-`, `.` and camelCase humps; it looks
secret when a word is `pass`, `passwd`, `password`, `passphrase`, `pwd`,
`secret` or `token`, a word ends in `password`, `passphrase`, `secret` or
`token` (`dbpassword`), or two adjacent words form `api_key`, `access_key`,
`private_key`, `secret_key` or `ssh_key` (`vault_pass`, `dbPassword`,
`db-password`, `sshKey`).

Before any POST, each target's `launch/` endpoint is read. A supplied flag
whose template setting `ask_*_on_launch` is false (AWX would silently ignore
it, for example running the whole inventory despite `--host-pattern`) is a usage
error naming the flag and template, unless the template has that value
already, which AWX treats as a no-op: its own value (credentials: every
supplied credential is already on the template), its project's branch for a
`--scm-branch` when the template sets none, or extra vars it saves with those
values. An empty `--extra-vars` mapping is never rejected. When
the template has a survey but does not prompt for variables, `--extra-vars`
may carry only the survey's variables; others are a usage error naming them.
Missing required survey variables (`variables_needed_to_start`) are reported
the same way. If AWX still lists
`ignored_fields` in a launch response, that row fails with the ignored field
names and keeps the execution ID; `awx test` reports such a case as an error
and cancels its job unless `--no-cancel`.

## Sync, wait for and follow executions

Project, inventory-source, and inventory synchronization use `sync`:

```bash
untaped awx projects sync Playbooks --wait
untaped awx inventory-sources sync Cloud --inventory Production --wait
untaped awx inventories sync Production --follow
```

Inventory sync first resolves and freezes the current source IDs (one
`inventory_sources` listing per 100 inventories), then uses
the same source update action for each source. A known unsupported, source-less,
manual, or otherwise invalid target fails complete preflight with zero POSTs;
`--continue-on-error` applies to runtime failures after preflight, not to an
invalid selection. `--dry-run` resolves and previews targets without
submitting an action.

`launch` and `sync` watch what they start with one flag family. `--wait`
waits for terminal success and exits nonzero for failed, canceled, or error
executions; each row then carries the execution's `status`, `started_at`
and `finished_at` (UTC, `2026-01-02T03:04:05Z`), the fields of the `awx.job`
record `jobs wait` prints. `--follow` waits the same way and streams each job's log to
stderr as it runs, ending with its PLAY RECAP (stdout keeps only the result
rows). With several executions each log line is prefixed with its
`[template]`. `--timeout SECONDS` (with `--wait` or `--follow`; zero or
more) stops waiting after that many seconds per execution: an execution still
running fails its row (`still running after --timeout 600s; it keeps running`),
and a `jobs wait` hint names it. Ctrl-C while waiting or following (including
`awx test run --parallel`) or while launches are still being submitted stops
promptly, exits 130, and prints the IDs of executions not known to have
finished (including ones AWX created while ignoring fields; "was launched"
when their status is unknown) with an `untaped awx jobs wait ...` command to
resume; the executions themselves keep running on the controller (`awx test
run` cancels them unless `--no-cancel`). `--cancel` (with `--wait` or
`--follow`, with or without `--timeout`) instead cancels every execution the
command stops watching, as soon as it stops watching it: one still running at
`--timeout`, one whose polling failed, and one AWX created while ignoring
fields (before the wait starts). Its row still fails, and
its `detail` ends with `cancel requested`, `it ended (successful) before the
cancel` (the row then shows that status), or `cancel failed: …` when AWX
refuses. Only a timed-out execution whose cancel failed keeps its `jobs wait`
hint. With `--cancel`, Ctrl-C cancels every execution not known to have
finished and prints `interrupted: <target>: job 101 cancel requested` without
a hint; a second Ctrl-C stops the cancel requests and names what may still
run. A failed or unreachable host shows
up in the followed log as Ansible prints it (`fatal: [host]: FAILED! => …`).
`--follow` (and `jobs logs --follow`) reads the log from the job's saved
events, which can trail its status, so it keeps reading briefly after the job
ends until they are all in, and warns on stderr when AWX is still saving them after that (the log
may be cut short; `jobs logs` later has it all). Log lines are written as
AWX stores them: never wrapped or tab-expanded, whatever the terminal width. Workflow jobs, including sliced launches that return a workflow job,
have no own events or stdout route, so following one prints its status
transitions from the detail endpoint instead of requesting
`workflow_events` or `stdout`. For structured per-task events, use
`jobs events --follow`.

For direct job inspection, non-default execution collections require an
explicit kind:

```bash
untaped awx jobs wait 101 --kind project_update
untaped awx jobs events 101 --kind inventory_update
untaped awx jobs logs 101 --kind project_update
```

`jobs events` and `jobs logs` accept several ids (or `--stdin`) and drain them
in order with a `[<id>]` breadcrumb on stderr. Without `--follow`, logs are
downloaded in full once, so large jobs return their whole output rather than
AWX's "too large to display" notice. With `--follow` (for `jobs logs` as for
`launch`/`sync --follow`), the log is read through the job's events: each poll
asks only for the new events and prints their output in order, without ANSI
colours, so following a long job never downloads its log again. An event AWX
saves late is printed once the ones before it arrive; one that never arrives
is reported on stderr. `--tail N --follow` reads only the newest events for
the last N lines, then follows from there; following a job that already
finished downloads its log once. Without `--follow`,
`--format json` or `yaml` prints one array holding every job's rows, and each
row names its `job`. With `--follow`, json streams one object per line
(NDJSON) as rows arrive.

`jobs list` shows the newest 20 executions by default; pass `--limit N` for a
different count or `--limit 0` for every record. `--template NAME|ID` keeps
the runs of one template (the project for `--kind project_update`, the
inventory source for `--kind inventory_update`); digits mean an AWX id, so
match a numeric name with `--filter job_template__name=123`.
`<kind> list --limit N` stops paging once N records are read. `--limit 0` means no limit on every awx list.

Every awx command's table shows a few default columns for a human scanning
rows; `json`, `yaml` and `pipe` carry the complete records unless
`--columns` narrows them, so read those instead of parsing a table.
`--columns +name` adds a column to the table and `--columns=-name` removes
one, and a column empty on every row is left out. `list` also applies its
default columns to `raw`. A `list` or `get` table shows foreign keys by name
(`inventory`, `organization`, `credential_type`) from the record's
`summary_fields`, without extra requests; `--with-names` does the same in
every format. `export` stays YAML by default.

| Command | Default table columns |
|---|---|
| `jobs list` | `id`, `name`, `status`, `launch_type`, `started`, `elapsed` |
| `jobs get` | `id`, `name`, `status`, `started`, `finished`, `elapsed`, `job_explanation` |
| `jobs wait` | `id`, `name`, `status` |
| `jobs events` | `counter`, `event`, `host_name`, `task`, `changed`, `failed` |
| `jobs cancel` | `id`, `name`, `action`, `detail` (the record's `status` is the one read before the cancel) |
| `jobs relaunch` | `id`, `name`, `status`, `target_id`, `action`, `detail` |
| `<kind> launch`, `sync` | `target_name`, `id`, `status`, `action`, `detail` (`payload` with `--dry-run`) |
| `apply` | `id`, `name`, `kind`, `action`, `fields_changed`, `detail` |
| `<kind> patch`, `edit` | `id`, `name`, `action`, `fields_changed`, `detail` |
| `<kind> delete` | `id`, `name`, `action`, `detail` |
| `<kind> <field> add`, `remove` | `id`, `name`, `action`, `associate`, `disassociate`, `detail` |
| `groups list` | `id`, `name`, `description`, `inventory` |
| `inventory-sources list` | `id`, `name`, `source`, `status`, `inventory` |
| `schedules list` | `id`, `name`, `unified_job_template` (the template it runs), `next_run`, `enabled` |

Launch and sync results are `awx.launch_outcome` and `awx.sync_outcome`
records (`jobs * --stdin` accepts them). Every preview row, including
`--dry-run` output, has the action `planned`, and apply/patch/edit results
report `fields_changed` and `preserved_secrets` as lists.

`--kind` accepts `job` (default), `workflow_job`, `project_update`,
`inventory_update`, and `ad_hoc_command`. Typed records piped with `--stdin`
(for example `launch --format pipe | untaped awx jobs wait --stdin`) carry
their own execution kind, which takes precedence over `--kind`.

Use `--kind workflow_job` only with operations supported by that execution
route, such as `jobs wait`; workflow job `events` and `logs` are rejected
without making an unsupported request. A polymorphic launch response with no
trusted kind fails rather than guessing an endpoint; a known submitted ID is
retained in the failed result.

## Inspect jobs

```bash
untaped awx jobs list --status failed --limit 10
untaped awx jobs get 101 102 --format yaml
untaped awx jobs logs 101 --tail 50 --follow
untaped awx jobs logs 101 --grep 'fatal:' -i -f json
untaped awx jobs events 101 --filter event=runner_on_failed
untaped awx jobs wait 101 --timeout 600
```

Cancel or relaunch existing executions. Both read every id first (an
unknown id rejects the batch before any POST), preview each target on
stderr, and ask once with No as the default; `--yes` skips the prompt and
`--dry-run` previews `planned` rows without writing:

```bash
untaped awx jobs cancel 101 102
untaped awx jobs relaunch 101 --failed-hosts --yes --format pipe \
  | untaped awx jobs wait --stdin
```

`cancel` rows are `awx.cancel_outcome`: `cancel_requested` (AWX stops the
execution asynchronously; `jobs wait` shows when it reaches `canceled`),
`skipped` for one that already finished, or `failed` when the controller
refuses. `relaunch` rows are `awx.relaunch_outcome`: `id` and `kind` name the
new execution and `target_id` the one it repeats. `--failed-hosts` reruns only
the failed hosts and applies to job executions; project and inventory updates
have no relaunch route (sync them instead).

`logs` prints the job's stdout; `events` prints the structured per-task
events. Both take `--follow` to tail a running job (`--follow` has no short
form; `-f` is `--format`, as everywhere). Chain a launch into a
wait or log tail through the pipe:

```bash
untaped awx job-templates launch Deploy --format pipe \
  | untaped awx jobs logs --stdin --follow
```

## Memberships

Credentials on a job template, labels on a job or workflow template, hosts
and child groups in a group, and input inventories and instance groups on an
inventory are managed with `add` and `remove`. Both are idempotent and preview before they write.

```bash
untaped awx job-templates credentials add Deploy "Vault prod" --organization Default
untaped awx workflow-templates labels add "Release train" nightly --organization Default
untaped awx groups hosts add web web-01 web-02 --inventory Production
untaped awx hosts list --inventory Production --search web --format pipe \
  | untaped awx groups hosts add web --inventory Production --stdin
untaped awx inventories input-inventories remove Constructed Legacy --dry-run
```

## Template SCM source

`job-templates list` and `get` accept `--with-scm` to add three fields read
from each template's project:

- `scm_url`: the project's SCM URL.
- `effective_scm_ref`: the ref a job checks out, which is the template's
  `scm_branch` when it is set and the project allows the override
  (`allow_override`), otherwise the project's `scm_branch`. An empty value
  stays empty (the repository's default branch); it is never guessed.
- `project_allow_override`: the project's `allow_override`.

```bash
untaped awx job-templates list --organization Default --with-scm \
  --columns name,scm_url,effective_scm_ref
untaped awx job-templates get Deploy --with-scm --format json
```

Each distinct project is read once per command. The fields appear in `json`,
`yaml` and `pipe` output and can be picked with `--columns`; the table view
shows them by default. A template without a project, or whose project cannot
be read (with a warning), gets `null` values.

## Find where a template is used

```bash
untaped awx job-templates usage Deploy --recursive
untaped awx workflow-templates nodes "Release train" --recursive --type job_template
untaped awx unified-templates list --type workflow_job_template
```

`usage` lists the workflow templates that contain a template (`--recursive`
walks up to the top-level workflows). `nodes` lists what a workflow contains
(`--recursive` expands nested workflows); `workflow-templates export` shows its
whole graph, edges and prompts included. When nothing matches, both say so
on stderr (`No containing workflows found.`, `No workflow nodes found.`). `unified-templates` is AWX's view
of every launchable kind.

## Test suites

`awx test` is [experimental](../stability.md#experimental) and may change in
a minor release.

`awx test` launches a job template with a matrix of parameters, checks each
job against what the case expects, and reports one result per case. Suites
live in the repository they test, under `.untaped/awx/tests/`. A suite file
is YAML: an optional `---` header declares variables, and the body, a Jinja2
template rendered with them, names the job template, the `defaults` every
case inherits, and the `cases`, each a `launch:` payload plus what it must
`expect:`.

The complete format (every field, header variables, Jinja2 rules, merge
rules, `!ref`, preflight), the `awx.test_result` record and example suites
ship with the CLI in the awx skill, so an agent reads the same pages: see the
awx skill's `references/test-suites.md` and `references/test-results.md`
([test suites](../../src/untaped/capabilities/awx/skills/untaped-awx/references/test-suites.md),
[test results](../../src/untaped/capabilities/awx/skills/untaped-awx/references/test-results.md),
[examples](../../src/untaped/capabilities/awx/skills/untaped-awx/examples/)),
installed with `untaped skills install awx`.

```bash
untaped awx test init "Deploy app"
untaped awx test init "Deploy app" --organization Ops --out suites/deploy.yml
untaped awx schema AwxTestSuite > awx-test-suite.schema.json
untaped awx test validate
untaped awx test run --scm-branch HEAD --format json
untaped awx test run --case deploy-smoke/web --var env=prod --show-logs
untaped awx test run --scm-branch main --format json > /tmp/baseline.json
untaped awx test run --scm-branch HEAD --compare /tmp/baseline.json
untaped awx test run --scm-branch HEAD --baseline main
untaped awx test validate --source-ref HEAD
untaped awx test run --source-ref HEAD --format json
untaped awx test prune --dry-run
```

- `test init TEMPLATE` reads the template's launch prompts and survey and
  writes a commented starter suite with one `smoke` case. Required survey
  variables get their default, else their first choice, else `TODO`; a
  password with a stored default gets `$encrypted$` (AWX then uses the
  stored value) and one without gets `TODO`. Optional survey variables and
  the fields the template prompts for are listed as comments. It writes
  `.untaped/awx/tests/<name>.yml` at the root of the git checkout (the
  template name lowercased, with `-` between words) unless `--out` names the
  file, prints the path, and never replaces an existing file.
- `awx schema KIND` prints the JSON Schema of a document you write,
  generated from the installed version's models (json by default,
  `--format yaml`); `AwxTestSuite` is the only kind so far. It describes the
  suite body; the header's variables are `readOnly` in it.
- The loop: `init` (or copy an example), edit the cases, commit and push,
  `validate` (every case is checked against its template without launching),
  then `run --scm-branch HEAD`, which is refused until HEAD is pushed.
  `run` exits 0 only when at least one case ran and every case passed. Each
  case that did not pass carries a `failure` saying which system is
  responsible (the suite, the credentials, the controller, the project or
  inventory update, the hosts, the playbook or the expectation), with its
  category, a message, a hint and the evidence from whichever execution
  failed. With `--format json`, `yaml` or `pipe` every row also carries each
  host's PLAY RECAP counters. The run exits with the most severe case: 4 when
  the environment needs fixing, 5 when retrying later may help, 1 when the
  change or the suite must. The awx skill's `references/test-results.md`
  lists the systems and what to do for each.
- Regression checks: besides `status` and `log`, a case's `expect:` can
  bound the changed tasks (`changed`) and each host's failed, unreachable or
  changed counters (`hosts`, `"*"` for every host), require the failed tasks
  that prove a negative case failed for the right reason (`failed_tasks`;
  `validate` warns about a `status: failed` case without it), and rerun the
  case to prove nothing changes the second time (`idempotent: true`). These
  checks, and `log`, read what AWX writes from the job's events once it has
  saved them: a job AWX is still saving fails the case as an error (exit 5,
  retry later), never a pass.
- `--compare FILE` compares a run with the saved JSON (or pipe) output of an
  earlier one, and `--baseline REF` runs every case on `REF` first, then
  compares. Each row gains `baseline` and a `change`, and only a regression
  or a failing new case fails the run.
- Workflow suites: `workflowTemplate: NAME` instead of `jobTemplate` launches
  a workflow job template (`test init NAME --workflow` writes a starter that
  lists its node ids). A case answers the workflow's approvals with
  `approvals: approve` or `deny` (without it, a pending approval fails the
  case at once and cancels the workflow), and checks each node's job under
  `expect.nodes` with the same checks as a case, plus `status: never_ran`.
  Each row lists the workflow's `nodes`, and a failed workflow is blamed on
  the node that failed it (`node deploy: …`, with that job's evidence).
- Temporary test sets: `run --source-ref REF` tests REF with the template
  configuration it carries.
  - Suites and the job template and workflow specs under `.untaped/awx/`
    are read at REF's commit, which must be pushed.
  - Each suite whose template has a spec runs a temporary copy of it, named
    `NAME [untaped-test SHA RUN]` and pinned to the commit (its project must
    allow branch override). The copy prompts for every field its cases set.
    A copied workflow's nodes run the copies of templates with specs.
  - Links are looked up by name and never created. Every other template the
    run launches must prompt for `scm_branch`, or the run is refused.
  - The copies are created without a confirmation and deleted after the
    run, even after Ctrl-C; `--keep` keeps them. A copy that could not be
    provisioned stops the run before any launch, never as a test failure.
  - `validate --source-ref REF` (or `run --dry-run`) checks it all without
    writing and prints the copies (`awx.provision_outcome`); `validate`
    takes `--format` and `--columns`.
  - `test prune` deletes the copies a killed run left (`--older-than`,
    default `2h`; `--run RUN` for one run's copies).
- To let an AI agent run suites against its own changes, give it a dedicated
  profile and token: see [AWX agent profile](./agent-profile.md). Temporary
  test sets need it to create and delete job templates and workflows in the
  organization.

## Confirmations, failures, and integrity

`patch`, `edit`, `apply`, and `delete` show one complete redacted preview and
ask once with No as the default. `--yes` skips the prompt. `--dry-run` never
writes and wins over `--yes`. Declining exits 1 with `cancelled; no changes
made`. Configuration writes without a controlling terminal require `--yes` or
`--dry-run` (exit 2 otherwise). `launch` and `sync` of a
single named target submit immediately; when more than one target is selected,
or the selection came from `--all`, `--filter`, `--search`, or `--stdin`, they
list the targets on stderr and ask once (No by default). `--yes` skips that
prompt and `--dry-run` previews without submitting. An
explicit `--allow-unverified --yes` can accept an unverified configuration
write, but the result remains labeled unverified. A configuration plan with no
changes does not prompt or write.

The complete batch is validated before the first write. Existing fields and
memberships are re-read to detect conflicts or deletion; this check cannot
close a race with a later controller request and provides no transaction or
rollback. `delete` re-reads its targets only after an interactive prompt;
with `--yes` the selection read just before the writes is the check. Resource bodies and memberships are verified separately, so a body
success with a membership failure is reported as `partial` and retains the
resource ID. So is a template write whose survey write then fails. Membership changes are additive for the membership commands;
replacement membership fields verify the exact set or declared order while
retaining unrelated members for additive operations.

A failed command exits with the most severe failure it met (see
[Exit codes](../reference/exit-codes.md)): 4 when the environment needs
fixing (AWX rejected the token, a permission is missing, or local setup such
as settings, `git`, or an unpushed `--scm-branch HEAD`), 5 when AWX was
unavailable (network error, timeout, 5xx or 429; retry later), and 1 when the
thing itself failed (an invalid input file, a name not found, a failed write
or job). With `--format json`, `yaml` or `pipe`, or `UNTAPED_DIAGNOSTICS=json`,
stderr carries one JSON object per line with the failure's `category`,
`system`, `retryable` and `hint`. A `failed`, `partial` or `conflict` row that
an error caused carries the same in its `error` field (`category`, `system`,
`retryable`, `message`, `hint`) next to its human `detail`; tables leave it
out.

Writes are serial by default. `--parallel N` is bounded at ten. A runtime
failure stops scheduling new work by default, while
`--continue-on-error` schedules independent remaining work; already-running
requests finish and are reported. Started execution IDs and target IDs remain
visible even if a later submission or monitor fails. Asynchronous inventory
deletion reports `deletion_requested`, not that the resource is already gone.

The old `apply --stdin --set ...` overlay interface is removed; use
`patch --stdin --set ...`. `projects update` is removed; use `projects sync`.
`--fail-fast` is removed; default runtime scheduling stops on failure, and
`--continue-on-error` opts into best effort. There are no compatibility aliases.

8.0 removed these spellings: `awx save` and `awx <kind> save` (use
`export`), `awx <kind> apply` (use `awx apply`), `launch --limit` (use
`--host-pattern`), `launch --inventory` (use `--launch-inventory`),
`launch`/`sync --track` (use `--follow`), `usage -r` and `nodes -r` (use
`--recursive`), and `jobs logs -f` as `--follow` (`-f` is `--format`).

`ping` options are keyword-only: use `awx ping -f json`, not `awx ping json`.

## Optional disposable live-AAP smoke

To try the write path safely, pick a disposable inventory and a harmless
source on a configured controller, run a smoke test like this, and restore the
exported files afterward:

```bash
untaped awx ping
untaped awx inventories export Disposable --organization Default \
  --out disposable-inventory.yml
untaped awx inventory-sources export DisposableSource --inventory Disposable \
  --inventory-organization Default --out disposable-source.yml

untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --inventory-organization Default \
  --set update_cache_timeout=0 --dry-run
untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --inventory-organization Default \
  --set update_cache_timeout=0 --yes
untaped awx inventory-sources get DisposableSource \
  --inventory Disposable --inventory-organization Default --format yaml

untaped awx inventory-sources edit DisposableSource \
  --inventory Disposable --inventory-organization Default --dry-run
untaped awx inventory-sources sync DisposableSource \
  --inventory Disposable --inventory-organization Default --follow

untaped awx apply disposable-source.yml --yes
untaped awx apply disposable-inventory.yml --yes
```

Confirm that the cache timeout changed, `update_on_launch` stayed unchanged,
the no-op editor made no write, and the sync reached the expected terminal
state. These steps are opt-in live writes against a disposable controller.

## See also

- [Getting started](../getting-started.md)
- [Pipes and record kinds](../reference/pipes.md)
- [Configuration reference](../reference/config.md#awx)
- [Exit codes](../reference/exit-codes.md)
