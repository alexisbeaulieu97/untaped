# Writing to issues

## Which writes ask first

`jira.confirm` decides:

| Value | Asks before |
|---|---|
| `destructive` (default) | destructive writes only |
| `always` | every write |
| `never` | no write |

A write is destructive when it can replace or remove what an issue holds:
`issues transition`, or an `issues patch` that sets a field, changes the
assignee, or has an `update` operation other than `add`. `issues create`,
`issues comment`, `issues links create` and add-only patches are not.

Whatever the policy, preview with `--dry-run` and wait for the user's
approval before sending with `--yes`.

## The preview

- It lists each request (`METHOD path`), then one `field: old → new` line per
  change. Long text is cut to 60 characters in the preview only; the full
  value is sent.
- For patches and transitions it reads the issue's current values, so it
  needs working credentials. A patch preview fails if the issue cannot be
  read; a transition preview shows `(unknown)`, or
  `(not available from this status)` for an `--id` the issue does not offer.
- Create, comment and link previews make no request.
- `--dry-run` prints the preview on stderr, emits a `planned` outcome and
  sends nothing. `--yes` skips both the prompt and the preview's reads.

## Fields

- `issues create` and `issues patch` take `--summary` and `--description`,
  `--set KEY=VALUE` for a string field and `--set-json KEY=JSON` for any
  field (both repeatable).
- `--fields-file FILE` supplies a Jira-shaped YAML or JSON document with
  `fields` and `update`; flags override it.
- `issues create` takes its project from `--project`, else
  `jira.default_project`.
- `issues comment` reads the body from `--body`, `--body-file FILE`, or stdin.

## Assigning

- `issues patch KEY --assignee USER` assigns (`@me` is the authenticated
  user); `--unassign` clears it. These override `fields.assignee` in a fields
  file.
- Assignment works even when the assignee field is not on the issue's edit
  screen.
- A patch that changes fields and the assignee sends the field edit first. If
  only the assignment fails, the error says the fields were already updated.

## Transitions

- Pass exactly one of `--to NAME` or `--id ID`; take both from
  `issues transitions KEY`.
- `--resolution NAME` and `--comment TEXT` go in the same request as the
  transition.

## Links

`issues links create KEY TYPE OTHER` reads "KEY outward-phrase OTHER": with
`Blocks`, KEY blocks OTHER. The preview prints a `reads as:` line in that
order. Check it on one pair before linking in bulk, since a reversed link
must be deleted in Jira by hand.

## The outcome record

Each write emits a `jira.issue_outcome` record with `action` (`created`,
`updated`, `commented`, `transitioned`, `linked` or `planned`); `--columns '?'`
lists the rest. Under `--dry-run` (`planned`) a new issue has no `key` yet.
Mutating requests are never retried automatically.
