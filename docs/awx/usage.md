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

Use `untaped --profile <name> awx ...` to select a different configured
profile. Tokens are secret settings; do not put them in a manifest or command
history.

The writable resource groups are job templates, workflow templates, projects,
schedules, hosts, groups, inventories, and inventory sources. They support the
shared `list`, `get`, `save`, `apply`, `patch`, `edit`, and `delete` lifecycle
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
untaped awx inventory-sources list --inventory Production --organization Default
untaped awx inventories patch Production --organization Default \
  --set update_cache_timeout=3600
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
mix bare lines and typed envelopes. Piped selections still use the controlling
terminal for confirmation when one is available; machine data stays on
stdout, while previews, prompts, progress, and warnings go to stderr.

## Patch shared fields

`patch` changes existing resources only. Repeat `--set KEY=VALUE` or provide a
YAML/JSON mapping with `--patch-file`; `--set` wins when both specify a field.
Values use JSON coercion when possible (`true`, `false`, numbers, arrays,
objects, and `null`). A supplied value replaces that top-level field, and an
omitted field is unchanged. Nested objects are not implicitly merged.

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
references fail before any write. Identity, parent, kind, and read-only fields
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

## Apply, save, and inventory lifecycle

`apply FILE_OR_DIRECTORY` is the declarative create/update path. It accepts
complete portable YAML documents, resolves dependencies, previews the full
batch once, and writes only after confirmation:

```bash
untaped awx apply ./awx-specs --dry-run
untaped awx inventories apply ./inventory.yml --yes
```

`save` exports a fixed selection as portable YAML. Per-resource save accepts
`--out FILE`; without it, YAML is written to stdout. Inventory and source
exports preserve organization and parent identity:

```bash
untaped awx inventories save Production --organization Default \
  --out inventory.yml
untaped awx inventory-sources save Cloud --inventory Production \
  --organization Default --out source.yml
```

Constructed inventory settings use the constructed inventory route and its
managed source. Operation support is specific to the workflow: inventory sync
rejects smart or source-less inventories and invalid or manual sources during
preflight, while apply accepts representable inventory documents and rejects
only incompatible source/configuration combinations. Editing inventory
settings does not recreate or rewrite source-managed hosts or groups. Workflow
template exports are partial: their node graph and edges are not round-tripped.

## Sync and track executions

Project, inventory-source, and inventory synchronization use `sync`:

```bash
untaped awx projects sync Playbooks --wait
untaped awx inventory-sources sync Cloud --inventory Production --wait
untaped awx inventories sync Production --wait --track
```

Inventory sync first resolves and freezes the current source IDs, then uses
the same source update action for each source. A known unsupported, source-less,
manual, or otherwise invalid target fails complete preflight with zero POSTs;
`--continue-on-error` applies to runtime failures after preflight, not to an
invalid selection. `--dry-run` resolves and previews targets without
submitting an action.

`--wait` waits for terminal success and exits nonzero for failed, cancelled, or
error executions. `--track` shows progress on stderr while waiting. Ordinary
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

Use `--kind workflow_job` only with operations supported by that execution
route, such as `jobs wait`; workflow job `events` and `logs` are rejected
without making an unsupported request. A polymorphic launch response with no
trusted kind fails rather than guessing an endpoint; a known submitted ID is
retained in the failed result.

## Confirmations, failures, and integrity

`patch`, `edit`, `apply`, and `delete` show one complete redacted preview and
ask once with No as the default. `--yes` skips the prompt. `--dry-run` never
writes; it is mutually exclusive with `--yes`. Configuration writes without a
controlling terminal require `--yes` or `--dry-run`. Launch and sync are
explicit actions and do not add a configuration-edit confirmation. An
explicit `--allow-unverified --yes` can accept an unverified configuration
write, but the result remains labeled unverified. A configuration plan with no
changes does not prompt or write.

The complete batch is validated before the first write. Existing fields and
memberships are re-read to detect conflicts or deletion; this check cannot
close a race with a later controller request and provides no transaction or
rollback. Resource bodies and memberships are verified separately, so a body
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

## Optional disposable live-AAP smoke

No live AAP controller was available or exercised during development; the
automated suite uses a strict HTTP fake. If you explicitly choose a disposable
inventory and harmless source on a configured controller, run a smoke test
like this and restore the saved files afterward:

```bash
untaped awx ping
untaped awx inventories save Disposable --organization Default \
  --out disposable-inventory.yml
untaped awx inventory-sources save DisposableSource --inventory Disposable \
  --organization Default --out disposable-source.yml

untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --organization Default \
  --set update_cache_timeout=0 --dry-run
untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --organization Default \
  --set update_cache_timeout=0 --yes
untaped awx inventory-sources get DisposableSource \
  --inventory Disposable --organization Default --format yaml

untaped awx inventory-sources edit DisposableSource \
  --inventory Disposable --organization Default --dry-run
untaped awx inventory-sources sync DisposableSource \
  --inventory Disposable --organization Default --wait --track

untaped awx inventory-sources apply disposable-source.yml --yes
untaped awx inventories apply disposable-inventory.yml --yes
```

Confirm that the cache timeout changed, `update_on_launch` stayed unchanged,
the no-op editor made no write, and the sync reached the expected terminal
state. These steps are opt-in live writes; they are instructions for a user's
disposable controller, not a claim of live validation here.
