---
description: "Read before creating, patching, commenting on, transitioning or linking issues: what the preview shows, which writes ask first, and what a write emits."
---

The writes are `issues create`, `issues patch`, `issues comment`,
`issues transition` and `issues links create`. Each one changes Jira for
everyone the moment it lands.

## The preview

The preview lists each request a write sends (`METHOD path`), then one
`field: old → new` line per change. Long text is cut to 60 characters in the
preview only; the full value is sent.

- `--dry-run` prints the preview on stderr, emits a `jira.issue_outcome`
  with `action` `planned`, and sends nothing, even with `--yes`.
- Patch and transition previews read the issue's current values, so they
  need working credentials. A patch preview fails when the issue can't be
  read. A transition preview shows `(unknown)` for a value it can't read,
  and `(not available from this status)` for an `--id` the issue doesn't
  offer.
- Create, comment and link previews make no request.

## Which writes ask first

`jira.confirm` decides. Under the default, `destructive`, a write asks first
when it can replace or remove what an issue holds: every `issues transition`,
and an `issues patch` that sets a field, changes the assignee, or has an
`update` operation other than `add`. `issues create`, `issues comment`,
`issues links create` and add-only patches are sent without asking.

When a write asks, it shows the preview with the question. `--yes` skips
both the question and the preview's reads.

## What a write emits

Each write emits a `jira.issue_outcome` record whose `action` is `created`,
`updated`, `commented`, `transitioned`, `linked`, or `planned` under
`--dry-run`. `untaped plugin schema jira.issue_outcome` lists its fields.

Writes are never retried. A write that exits 5 may still have landed:
`untaped jira issues get KEY` shows whether it did.
