---
description: "Read before changing anything on an AWX controller: how a write previews and confirms, how a batch runs and fails, what is verified, and what can't be undone."
---

Every write (a command marked `(changes things)` or `(destructive)`)
previews once and asks once, No by default. What to select: `untaped agents awx selecting`.

## Preview and confirmation

- `--dry-run` never writes, even with `--yes`; `apply --check` reports
  drift. The preview has one row per planned change (resource, field, old →
  new).
- Without a terminal, a write needs `--yes` or `--dry-run` (else exit 2).
- A single named `launch` or `sync` submits at once; several targets, a
  query or a `--stdin` selection are listed and confirmed once.
- Declining exits 1 with `cancelled; no changes made`.

## How a batch runs

- Writes run one at a time unless `--parallel`. A failure fails its row and
  the rest still run, except a row that needs a resource whose create failed.
  `--fail-fast` stops scheduling new writes after the first failure, and a
  rejected token or broken configuration always does; writes already running
  finish and the rest are skipped.
- There is no transaction or rollback; result rows keep the ids they reached.
- Each write re-reads what it changes to detect a conflicting change.
  `delete` re-reads after an interactive prompt; with `--yes` the read just
  before the writes is the check.
- A body write whose membership or survey write then fails is `partial`,
  keeping the resource id.
- A write AWX does not read back as sent fails as unverified;
  `--allow-unverified` keeps it, still labelled unverified.

## Secrets

Previews and results redact known secrets. Keep `$encrypted$` placeholders as
they are: they preserve stored secrets. `awx.apply_outcome` rows list
`fields_changed`, `preserved_secrets`, `dropped_undeclared_secrets`
(`$encrypted$` placeholders at fields untaped doesn't treat as secrets, left
out of the write) and `dropped_secrets` (secret placeholders a create left
out, a new workflow node's `$encrypted$` extra vars included, so the new
resource or node starts without them).

## Recovery

There is no rollback, and a failed batch keeps what it wrote. An export taken
before the change (`job-templates export Deploy --out deploy-before.yml`) is a
recovery point: applying it restores the fields of a resource that still
exists. A deleted resource comes back with a new id and without its secrets,
access, schedules or history; a recreated template needs a new callback key
if it used one. What a document can't carry: `untaped agents awx documents`.
