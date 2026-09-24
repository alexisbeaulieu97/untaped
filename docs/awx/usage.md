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
`untaped config set awx.token_command '["pass", "show", "aap/token"]'`; see
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
both lookup and server filters. Selection-based mutation commands require an
explicit selection or `--all`.

```bash
untaped awx job-templates list --filter name__icontains=deploy
untaped awx inventory-sources list --inventory Production --inventory-organization Default
untaped awx inventory-sources patch Cloud --inventory Production \
  --inventory-organization Default --set update_cache_timeout=3600
```

`--filter` is repeatable and is passed to AWX using its server-side lookup
syntax. Names that remain ambiguous require a narrower scope or an ID. The
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

`apply FILE_OR_DIRECTORY` is the declarative create/update path. It accepts
complete portable YAML documents, resolves dependencies, previews the full
batch once, and writes only after confirmation:

```bash
untaped awx apply ./awx-specs --dry-run
untaped awx inventories apply ./inventory.yml --yes
```

A directory contributes every `*.yml` and `*.yaml` file, so keep other YAML
(for example CI or vars files) out of it; a file that cannot be read or parsed,
or holds an unknown or unexpected kind, fails the apply with its path named.
A document of an
organization-scoped kind without `metadata.organization` is scoped by
`awx.default_organization`, as selection and `awx test` are. With no default
configured, a name that exists in more than one organization is an ambiguity
error rather than a guess. A `spec.organization` name is used as the identity
when metadata omits one, and an explicit `metadata.organization: null` means
the org-less record (for example a global workflow template); `export` writes
that null for org-less records so an export/apply round trip never lands in the
default organization.

Relationship lists (template `credentials` and `labels`, group
`hosts`/`children`, inventory `instance_groups`) are replaced by adding new
members before removing old ones, so a refused add never leaves a template
without its credentials. Only a
credential that shares a type with an incoming one is removed first (AWX allows
one per type); if the add then fails, the removed members are re-added and the
row reports `partial`.

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
conflicting values for those fields through the two resources. Workflow
template exports are partial: their node graph and edges are not round-tripped.

### Template export round trip

A job or workflow template export carries its settings, `extra_vars`,
`credentials` and `labels` (by name), and its survey. Applying that file under
another `metadata.name` creates a template with the same non-secret
configuration:

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
# edit metadata.name to "Deploy next", then:
untaped awx job-templates apply deploy.yml --yes
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
  environment, credentials, labels) travel by name, so they must already exist
  where the file is applied.
- **Workflow graphs.** A workflow template export has no nodes or edges.

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

## Launch templates

`launch` submits job or workflow templates. `--extra-vars` is repeatable and
merged left to right into one mapping sent as JSON:

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
```

Before any POST, each target's `launch/` endpoint is read. A supplied flag
whose template setting `ask_*_on_launch` is false (AWX would silently ignore
it, for example running the whole inventory despite `--host-pattern`) is a usage
error naming the flag and template, unless the value equals the template's own
(credentials: every supplied credential is already on the template), which
AWX treats as a no-op. An empty `--extra-vars` mapping is never rejected. When
the template has a survey but does not prompt for variables, `--extra-vars`
may carry only the survey's variables; others are a usage error naming them.
Missing required survey variables (`variables_needed_to_start`) are reported
the same way. If AWX still lists
`ignored_fields` in a launch response, that row fails with the ignored field
names and keeps the execution ID; `awx test` reports such a case as an error.

## Sync and track executions

Project, inventory-source, and inventory synchronization use `sync`:

```bash
untaped awx projects sync Playbooks --wait
untaped awx inventory-sources sync Cloud --inventory Production --wait
untaped awx inventories sync Production --wait --track
```

Inventory sync first resolves and freezes the current source IDs (one
`inventory_sources` listing per 100 inventories), then uses
the same source update action for each source. A known unsupported, source-less,
manual, or otherwise invalid target fails complete preflight with zero POSTs;
`--continue-on-error` applies to runtime failures after preflight, not to an
invalid selection. `--dry-run` resolves and previews targets without
submitting an action.

`--wait` waits for terminal success and exits nonzero for failed, canceled, or
error executions. `--timeout SECONDS` (with `--wait` or `--track`) stops
waiting after that many seconds per execution: an execution still running
fails its row (`still running after --timeout 600s; it keeps running`), and a
`jobs wait` hint names it. Ctrl-C while waiting or tracking (including
`awx test run --parallel`) or while launches are still being submitted stops
promptly, exits 130, and prints the IDs of executions not known to have
finished (including ones AWX created while ignoring fields; "was launched"
when their status is unknown) with an `untaped awx jobs wait ...` command to
resume; the executions themselves keep running on the controller. `--track`
shows progress on stderr while waiting; a failed or unreachable host result
is followed by the reason from that event's output (up to ten lines; `jobs
events` has the rest). Ordinary
jobs expose `job_events`; project and inventory updates expose their `events`
routes. Workflow jobs, including sliced launches that return a workflow job,
have no own events or stdout route, so tracking emits status transitions from
the detail endpoint instead of requesting `workflow_events` or `stdout`.

For direct job inspection, non-default execution collections require an
explicit kind:

```bash
untaped awx jobs wait 101 --kind project_update
untaped awx jobs events 101 --kind inventory_update
untaped awx jobs logs 101 --kind project_update
```

`jobs events` and `jobs logs` accept several ids (or `--stdin`) and drain them
in order with a `[<id>]` breadcrumb on stderr. Without `--follow`,
`--format json` or `yaml` prints one array holding every job's rows, and each
row names its `job`. With `--follow`, json streams one object per line
(NDJSON) as rows arrive.

`jobs list` shows the newest 20 executions by default; pass `--limit N` for a
different count or `--limit 0` for every record. `--template NAME|ID` keeps
the runs of one template (the project for `--kind project_update`, the
inventory source for `--kind inventory_update`); digits mean an AWX id, so
match a numeric name with `--filter job_template__name=123`.
`<kind> list --limit N` stops paging once N records are read. `--limit 0` means no limit on every awx list.

`get` prints a table of the default columns; pass `--format yaml` or
`--format json` for the complete records. `export` stays YAML by default.
`list` applies its default columns to `table` and `raw` only; `json`, `yaml`
and `pipe` carry the complete records unless `--columns` narrows them.

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
untaped awx jobs logs 101 --grep 'fatal:' -i
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
events. Both take `--follow` to tail a running job. Chain a launch into a
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
(`--recursive` expands nested workflows). `unified-templates` is AWX's view
of every launchable kind.

## Test suites

`awx test` launches a job template with a matrix of parameters and reports
one pass or fail per case. A test file is YAML. An optional `---`-delimited
header declares variables; the body is a Jinja2 template rendered with them:

```yaml
---
variables:
  env: {type: choice, choices: [staging, prod], default: staging}
---
kind: AwxTestSuite
name: deploy-smoke
jobTemplate: Deploy app
defaults:
  launch:
    extra_vars: {dry_run: true}
cases:
  web:
    launch:
      limit: "web-{{ env }}"
  db:
    launch:
      limit: "db-{{ env }}"
      inventory: !ref {kind: Inventory, name: "{{ env }} inventory"}
```

```bash
untaped awx test validate tests/awx/
untaped awx test list tests/awx/deploy-smoke.yml --var env=prod
untaped awx test run tests/awx/ --var env=prod --parallel 4 --show-logs
untaped awx test run tests/awx/deploy-smoke.yml --case web --non-interactive
```

- `launch` holds the AWX launch payload fields. `!ref {kind, name}` resolves a
  resource name to its ID.
- A variable without a default is required: pass `--var`, `--vars-file`, or
  answer the prompt. `--non-interactive` fails instead of prompting.
- `run` exits 1 unless at least one case ran and every case passed.

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
resource ID. Membership changes are additive for the membership commands;
replacement membership fields verify the exact set or declared order while
retaining unrelated members for additive operations.

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

These spellings were renamed and keep working with a deprecation warning
until 8.0:

| Old | New |
|---|---|
| `awx save`, `awx <kind> save` | `awx export`, `awx <kind> export` |
| `launch --limit` | `launch --host-pattern` |
| `jobs logs -f` | `jobs logs --follow` |
| `usage -r`, `nodes -r` | `--recursive` |
| `inventories input_inventories`, `instance_groups` | `input-inventories`, `instance-groups` |

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
  --inventory Disposable --inventory-organization Default --wait --track

untaped awx inventory-sources apply disposable-source.yml --yes
untaped awx inventories apply disposable-inventory.yml --yes
```

Confirm that the cache timeout changed, `update_on_launch` stayed unchanged,
the no-op editor made no write, and the sync reached the expected terminal
state. These steps are opt-in live writes against a disposable controller.

## See also

- [Getting started](../getting-started.md)
- [Pipes and record kinds](../reference/pipes.md)
- [Configuration reference](../reference/config.md#awx)
- [Exit codes](../reference/exit-codes.md)
