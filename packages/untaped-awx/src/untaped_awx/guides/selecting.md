---
description: "Read before selecting AWX resources for a read or a write: selection modes, scopes and filters, how names resolve, and piping records between awx commands."
---

## Selection modes

Selection modes are exclusive: names, names with `--by-id`, `--stdin`,
`--filter`/`--search`, or `--all`. Commands that change things need an
explicit selection or `--all`, which selects every resource in scope.

```console
$ untaped awx inventory-sources list --inventory Production --inventory-organization Default
$ untaped awx inventory-sources patch Cloud --inventory Production --inventory-organization Default --set update_cache_timeout=3600 --dry-run
```

- Scope options (`--organization`, `--inventory`, `--inventory-organization`,
  `--parent`) constrain both the name lookup and filters. A group offers only
  the scopes its kind has; any other is a usage error (exit 2).
- `--filter KEY=VALUE` passes AWX's lookup syntax through
  (`name__icontains=web`, `inventory__name=Production`).
- A name that is not found names its scope and suggests close names. A
  second line says when `awx.default_organization` chose the organization;
  pass `--organization` to search elsewhere.
- A name in several organizations is ambiguous: pass `--organization`, set
  `awx.default_organization`, or select by id.
- The whole selection is resolved and validated before the first write; an
  empty or invalid selection writes nothing.
- Tables name foreign keys; `json`, `yaml` and `pipe` carry complete records
  with ids unless `--columns` narrows them or `--with-names` names them.
- Credentials, credential types, organizations and unified templates are
  read-only.

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

## What writes print

Writes emit outcome rows (`awx.apply_outcome` for `apply`, `patch` and
`edit`; `awx.delete_outcome`, `awx.copy_outcome`, `awx.rename_outcome`, …)
whose `action` is `planned` in a preview. A failed row carries an `error`
with `category`, `system`, `retryable`, `message` and `hint`; with
`--format json`, stderr carries the same fields as JSON Lines. How a batch of
writes runs: `untaped docs awx guides writes`.
