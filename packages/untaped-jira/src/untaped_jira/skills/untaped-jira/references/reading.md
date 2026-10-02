# Reading issues

## Which query runs

- `issues assigned` always starts from `jira.assigned_jql` (by default the
  user's unresolved issues). `--jql` and the shortcut flags are ANDed onto
  it, so they can only narrow it.
- `issues search` uses `--jql` and the shortcut flags; with none of them it
  falls back to `jira.assigned_jql`. For an unrestricted query pass `--jql`.
- An `ORDER BY` in `--jql` replaces the default `updated DESC` order.
- `--assignee @me` means the authenticated user. `--sprint` takes a sprint
  id, a sprint name, or `openSprints()`, `futureSprints()` or
  `closedSprints()`.
- Searches retry HTTP 429 and 503 on their own.

## What comes back

- Search rows hold the fields most triage needs; `--columns '?'` lists them.
  `url` opens in a browser; `api_url` is the REST address.
- `issues get` adds the detail fields. `comments` is `null` unless
  `--comments` fetched them.
- Timestamps are UTC, as `2026-01-02T03:04:05Z`.
- One issue renders as a key: value view in a table and as a single JSON
  object; several render as a table or a JSON array.

## Links

`links` is always a list, empty when there are none. Each entry has `key`,
`summary`, `status`, `type`, `direction` and `relation`:

- `direction` is `outward` or `inward`, seen from the issue you read;
- `relation` is Jira's phrase for it, such as `blocks` or `is blocked by`.

Linked issues are not fetched in full; `issues get` their keys for details.

## Comments and transitions

- `issues comments list KEY` emits `jira.comment` records with `author`,
  timestamps and `body`.
- `issues transitions KEY` emits the transitions the issue offers from its
  current status: `id`, `name` and `to_status` (null when Jira does not say).

## Several issues and pipes

- `issues get` and `issues transition` take several keys, or `--stdin` with
  bare keys or `jira.issue` / `jira.issue_outcome` records from
  `--format pipe`; any other record kind exits 2.
- Keys are `PROJECT-123` or a numeric id, case-insensitive. Anything else is a
  usage error (exit 2).
- `untaped jira sprints list` needs `--board-id` or `jira.default_board_id`.
