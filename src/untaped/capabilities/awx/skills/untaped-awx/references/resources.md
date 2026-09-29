# Selecting and changing resources

Every resource group (`job-templates`, `workflow-templates`, `projects`,
`schedules`, `hosts`, `groups`, `inventories`, `inventory-sources`) shares
`list`, `get`, `export`, `patch`, `edit` and `delete`; job and workflow
templates add `copy`, `rename`, `launch` and `usage`; projects, inventories
and inventory sources add `sync`. Credentials, credential types,
organizations and unified templates are read-only views. Creating resources
and changing many fields at once go through documents and `untaped awx apply`
([specs.md](specs.md)).

## Select resources

Selection modes are exclusive: positional names, names with `--by-id` (AWX
ids), `--stdin`, `--filter`/`--search`, or `--all`. Commands that change
things need an explicit selection or `--all`.

```bash
untaped awx job-templates list --filter name__icontains=deploy
untaped awx inventory-sources list --inventory Production --inventory-organization Default
untaped awx job-templates get Deploy --organization Default --format yaml
```

- Scope options constrain both the name lookup and filters. Each group offers
  only its own: `--organization` for organization-scoped kinds (templates,
  projects, inventories, credentials), `--inventory`,
  `--inventory-organization` and `--parent` for hosts, groups and inventory
  sources, `--parent` for schedules. Any other scope option is unknown to
  that group (exit 2).
- `--filter KEY=VALUE` is repeatable and passed to AWX in its lookup syntax
  (`name__icontains=web`, `inventory__name=Production`).
- A name that is not found names its scope and suggests close names
  (`JobTemplate not found: 'deplyo' in organization 'Default'; did you mean
  'deploy'?`); a second line says when `awx.default_organization` chose the
  organization (pass `--organization` to search elsewhere). An ambiguous name
  needs a narrower scope or an id.
- The whole selection is resolved and validated before the first write; an
  empty or invalid selection writes nothing.
- `list` shows default columns in `table`/`raw` only; `json`, `yaml` and
  `pipe` carry complete records unless `--columns` narrows them. `--limit N`
  stops after N records and `--limit 0` means all. `get` prints a table of
  default columns; use `--format yaml` or `--format json` for full records.
- `job-templates list|get --with-scm` adds `scm_url`, `effective_scm_ref`
  (the template's `scm_branch` when set and the project allows the override,
  else the project's `scm_branch`; empty stays empty) and
  `project_allow_override`; each project is read once per command, and a
  template without a readable project gets `null` values.

## Pipes between commands

`--format pipe` emits typed records carrying their kind and id, and a
`--stdin` consumer of the same kind uses the ids directly:

```bash
untaped awx job-templates list --filter name__icontains=deploy --format pipe \
  | untaped awx job-templates patch --stdin --set verbosity=2 --dry-run
```

Bare stdin lines are names (ids with `--by-id`); do not mix them with
records. A record of another kind (for example `awx.host` piped into
`projects patch`) exits 2, and empty stdin is an error. `--stdin` selection of
a kind also accepts that kind's `awx.copy_outcome` and `awx.rename_outcome`
records. Confirmations read the terminal even when stdin is piped.

Writes emit outcome rows (`awx.apply_outcome` for `apply`, `patch` and
`edit`; `awx.delete_outcome`, `awx.copy_outcome`, `awx.rename_outcome`, …)
whose `action` is `planned` in a preview.

## Patch fields

`patch` sets the same fields on existing resources:

```bash
untaped awx inventory-sources patch --filter inventory__name=Production \
  --set update_cache_timeout=3600 --dry-run
untaped awx job-templates patch --filter name__icontains=deploy \
  --patch-file changes.yml --set verbosity=2 --dry-run
```

- `--set KEY=VALUE` is repeatable and JSON-decoded (`true`, numbers, arrays,
  objects, `null`), except that a field the record holds as a string stays a
  string unless the value is a JSON object or array (`scm_branch=1.10` stays
  `"1.10"`). `--patch-file` takes a YAML/JSON mapping; `--set` wins over it.
- A value replaces the whole top-level field; omitted fields are unchanged,
  nested maps are not merged, an empty map clears one, and lists keep their
  order.
- An unknown field name close to a known one is refused with "did you mean"
  (exit 2) unless `--allow-unknown-fields`; other unknown names are sent with
  a warning.
- Foreign keys: an integer is an AWX id, a string is a name in the selected
  scope. Keep a numeric-looking name a string with JSON quotes:
  `--set 'inventory="123"'` (unquoted `inventory=123` is id 123).
- Inventory cache timeouts are seconds and `0` is valid; changing
  `update_cache_timeout` does not change `update_on_launch`.
- `patch` cannot create, rename, reparent or retarget, nor change identity,
  parent, kind or read-only fields. Use `untaped awx apply` to create and
  `rename` to rename.
- A schedule's survey password answers come back as `$encrypted$` in
  `extra_data`: applying them back keeps the stored answers, and changing
  another `extra_data` key beside one is refused. A real answer typed into
  `extra_data` is not recognised as secret and shows in previews.

## Edit in an editor

`edit` opens one YAML multi-document batch of the selection in `$VISUAL` or
`$EDITOR` (an editor that waits, such as `code --wait`); `--field` (repeatable)
limits the editable fields:

```bash
untaped awx job-templates edit --filter name__icontains=deploy --field inventory --field verbosity
```

Removing a document deselects it; removing a field leaves it unchanged; a
nested value replaces the whole field. Identity, name and parent changes, new
or duplicate documents are refused. `edit` needs a real terminal at
`/dev/tty` even with piped stdin or `--yes`, so an agent without one should
use `patch` or `apply`. A failed session keeps the file and prints its path;
a session with no change neither prompts nor writes.

## Copy and rename templates

```bash
untaped awx job-templates copy Deploy --name "Deploy next" --organization Default --dry-run
untaped awx job-templates rename Deploy "Deploy app" --organization Default --dry-run
```

- `copy SOURCE --name NEW` (job and workflow templates) copies server-side in
  the source's organization. It refuses a taken name, the source's own name,
  or a source AWX cannot copy (`can_copy: false`) before writing, and warns
  about parts AWX will not carry (listed in `not_carried`). It emits
  `awx.copy_outcome` (`id` is the new template).
- `rename SOURCE NEW` refuses a name already used in the same scope, previews
  old → new, and re-reads the template: a name AWX did not take fails the row
  (exit 1). It emits `awx.rename_outcome` (`id`, `name`, `old_name`, `kind`,
  `action`: `planned`, `renamed` or `failed`).

## Memberships

Credentials on a job template, labels on a job or workflow template, hosts and
child groups in a group, input inventories and instance groups on an
inventory use `add` and `remove`; both are idempotent and preview first:

```bash
untaped awx job-templates credentials add Deploy "Vault prod" --organization Default
untaped awx groups hosts add web web-01 web-02 --inventory Production
```

## Where templates are used

```bash
untaped awx job-templates usage Deploy --recursive
untaped awx workflow-templates nodes "Release train" --recursive --type job_template
untaped awx unified-templates list --type workflow_job_template
```

`usage` lists the workflows that contain a template (`--recursive` walks up
to the top-level workflows); `nodes` lists what a workflow contains
(`--recursive` expands nested workflows).

## Confirmations and batches

- `patch`, `edit`, `apply`, `delete`, `copy` and `rename` show one redacted
  preview and ask once, No by default. `--yes` skips the prompt; `--dry-run`
  never writes and wins over `--yes`. Declining exits 1 with `cancelled; no
  changes made`. Without a terminal they need `--yes` or `--dry-run` (exit 2).
- Writes run one at a time; `--parallel N` allows up to ten at once. A
  failure stops scheduling new writes unless `--continue-on-error`; writes
  already running finish, and results keep the ids they reached. There is no
  transaction or rollback.
- Each write re-reads what it changes to detect conflicting changes; a body
  write whose membership or survey write then fails is reported `partial`.
  Deleting an inventory is asynchronous (`deletion_requested`).
- Previews and results redact known secrets. `apply`, `patch` and `edit`
  emit `awx.apply_outcome` rows whose `fields_changed` and
  `preserved_secrets` are lists; every preview row has the action `planned`.
