# Selecting and changing resources

- [Select resources](#select-resources)
- [Pipes between commands](#pipes-between-commands)
- [Patch fields](#patch-fields)
- [Edit in an editor](#edit-in-an-editor)
- [Copy and rename templates](#copy-and-rename-templates)
- [Memberships](#memberships)
- [Where templates are used](#where-templates-are-used)
- [Confirmations and batches](#confirmations-and-batches)

`untaped awx <group> --help` lists each group's verbs and options. Creating
resources, and changing many fields at once, go through documents and
`untaped awx apply` ([specs.md](specs.md)).

## Select resources

Selection modes are exclusive: names, names with `--by-id`, `--stdin`,
`--filter`/`--search`, or `--all`. Commands that change things need an
explicit selection or `--all`.

```bash
untaped awx inventory-sources list --inventory Production --inventory-organization Default
untaped awx inventory-sources patch Cloud --inventory Production \
  --inventory-organization Default --set update_cache_timeout=3600
```

- Scope options (`--organization`, `--inventory`, `--inventory-organization`,
  `--parent`) constrain both the name lookup and filters. A group offers only
  the scopes its kind has; any other is a usage error (exit 2).
- `--filter KEY=VALUE` passes AWX's lookup syntax through
  (`name__icontains=web`, `inventory__name=Production`).
- A name that is not found names its scope and suggests close names. A
  second line says when `awx.default_organization` chose the organization;
  pass `--organization` to search elsewhere.
- An ambiguous name needs a narrower scope or an id.
- The whole selection is resolved and validated before the first write; an
  empty or invalid selection writes nothing.
- Tables name foreign keys; `json`, `yaml` and `pipe` carry complete records
  with ids unless `--columns` narrows them or `--with-names` names them.
- `job-templates list|get --with-scm` reads each project once; a template
  without a readable project gets `null` SCM values.
- `get` on a job or workflow template includes its survey questions
  (`survey_spec`); password defaults read as `$encrypted$`.

## Pipes between commands

`--format pipe` emits typed records carrying their kind and id, and a
`--stdin` consumer of the same kind uses the ids directly:

```bash
untaped awx job-templates list --filter name__icontains=deploy --format pipe \
  | untaped awx job-templates patch --stdin --set verbosity=2 --dry-run
```

- Bare stdin lines are names (ids with `--by-id`); do not mix them with
  records.
- A record of another kind (an `awx.host` piped into `projects patch`) exits
  2, and empty stdin is an error.
- `--stdin` also accepts the kind's `awx.copy_outcome` and
  `awx.rename_outcome` records.
- Confirmations read the terminal even when stdin is piped.

Writes emit outcome rows (`awx.apply_outcome` for `apply`, `patch` and
`edit`; `awx.delete_outcome`, `awx.copy_outcome`, `awx.rename_outcome`, …)
whose `action` is `planned` in a preview. A failed row carries an `error`
with `category`, `system`, `retryable`, `message` and `hint`; with
`--format json`, stderr carries the same fields as JSON Lines.

## Patch fields

`patch` sets the same fields on every selected resource:

```bash
untaped awx job-templates patch --filter name__icontains=deploy \
  --patch-file changes.yml --set verbosity=2 --dry-run
```

- `--set` values are JSON-decoded (`true`, numbers, arrays, objects,
  `null`). A field the record holds as a string stays a string unless the
  value is a JSON object or array: `scm_branch=1.10` stays `"1.10"`.
- A value replaces the whole top-level field: nested maps are not merged, an
  empty map clears one, lists keep their order, omitted fields are unchanged.
- An unknown field close to a known one is refused with "did you mean"
  (exit 2); other unknown names are sent with a warning.
- Foreign keys: an integer is an AWX id, a string a name in the selected
  scope. Keep a numeric-looking name a string with JSON quotes:
  `--set 'inventory="123"'`.
- An ambiguous, missing or out-of-scope reference fails before any write.
- Inventory cache timeouts are seconds and `0` is valid; changing
  `update_cache_timeout` leaves `update_on_launch` alone.
- `patch` cannot create, rename, reparent or retarget, nor change identity,
  kind or read-only fields.

A schedule's survey password answers read back as `$encrypted$` in
`extra_data`:

- applying them back keeps the stored answers;
- changing another `extra_data` key beside one (removing an answer included)
  is refused;
- a new schedule drops them with a warning;
- a real answer typed into `extra_data` is not recognised as secret and shows
  in previews.

## Edit in an editor

`edit` opens the selection as one YAML multi-document batch in `$VISUAL` or
`$EDITOR` (an editor that waits, such as `code --wait`); `--field` limits the
editable fields.

- It needs a real terminal at `/dev/tty`, even with piped stdin or `--yes`;
  an agent without one uses `patch` or `apply`.
- Removing a document deselects it; removing a field leaves it unchanged; a
  nested value replaces the whole field.
- Identity, name and parent changes, and new or duplicate documents, are
  refused.
- Invalid YAML can be reopened or cancelled; a session with no change
  neither prompts nor writes.
- The file is owner-only and removed after a clean session; a failed session
  keeps it and prints its path.

## Copy and rename templates

```bash
untaped awx job-templates copy Deploy --name "Deploy next" --organization Default --dry-run
untaped awx job-templates rename Deploy "Deploy app" --organization Default --dry-run
```

- `copy` (job and workflow templates) copies server-side in the source's
  organization. It refuses a taken name, the source's own name, or a source
  AWX cannot copy (`can_copy: false`) before writing.
- `copy` warns about parts AWX will not carry without user input, such as a
  workflow's references the caller cannot use; its `awx.copy_outcome` lists
  them in `not_carried`, and its `id` is the new template's.
- `rename` refuses the current name and a name already used in the same
  scope (the organization, or none for an org-less workflow).
- `rename` re-reads the template: a name AWX did not take fails the row
  (exit 1). Its `awx.rename_outcome` carries `old_name`.

## Memberships

Credentials on a job template, labels on a template, hosts and child groups
in a group, and input inventories and instance groups on an inventory use
`add` and `remove`. Both are idempotent and preview first:

```bash
untaped awx job-templates credentials add Deploy "Vault prod" --organization Default
untaped awx groups hosts add web web-01 web-02 --inventory Production
```

## Where templates are used

`untaped awx job-templates usage Deploy --recursive` lists the workflows that
contain a template, up to the top-level ones. `workflow-templates nodes
--recursive` lists what a workflow contains, nested workflows expanded.
Run `usage` before deleting a template: a workflow node that ran it runs
nothing afterwards.

## Confirmations and batches

The preview, `--dry-run`, `--yes` and exit-code rules are in the skill's
"Destructive operations" section. Beyond them:

- Writes run one at a time; `--parallel N` allows up to ten. A failure stops
  scheduling new writes unless `--continue-on-error`; writes already running
  finish.
- There is no transaction or rollback; result rows keep the ids they reached.
- Each write re-reads what it changes to detect a conflicting change.
  `delete` re-reads after an interactive prompt; with `--yes` the read just
  before the writes is the check.
- A body write whose membership or survey write then fails is `partial`,
  keeping the resource id.
- A write AWX does not read back as sent fails as unverified;
  `--allow-unverified --yes` keeps it, still labelled unverified.
- Deleting an inventory is asynchronous (`deletion_requested`).
- Previews and results redact known secrets. `awx.apply_outcome` rows list
  `fields_changed`, `preserved_secrets` and `dropped_undeclared_secrets`
  (`$encrypted$` placeholders at fields untaped doesn't treat as secrets,
  left out of the write).
