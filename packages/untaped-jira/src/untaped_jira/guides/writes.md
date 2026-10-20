---
description: "Read before creating, patching, commenting on, transitioning or linking issues: what the preview shows, which writes ask first, and what a write emits."
---

## The preview

The preview lists each request a write sends (`METHOD path`), then one
`field: old → new` line per change. A field's long text is cut to 60
characters in the preview only; the full value is sent. A comment prints
whole.

- `--dry-run` prints the preview on stderr, emits a `jira.issue_outcome`
  with `action` `planned`, and sends nothing, even with `--yes`.
- Patch and transition previews read the issue's current values, so they
  need working credentials; a patch reads the fields it sets and, when
  assigning, the assignee. A patch preview fails when the issue can't be
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

Each write emits a `jira.issue_outcome` record;
`untaped plugin schema jira.issue_outcome` describes it.

Writes are sent once, never retried: Jira's 429 and 503 answers are retried
only for reads.
A write that exits 5 may still have landed: `untaped jira issues get KEY`
shows whether it did.
