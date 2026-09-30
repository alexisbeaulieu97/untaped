# Jira

`untaped jira` searches, creates, updates, comments on and transitions Jira
issues, and looks up projects, boards and sprints. It targets Jira Data Center
and self-hosted Jira (REST API v2 and Agile 1.0), not Jira Cloud.

## Set up

Create a personal access token in Jira, then:

```bash
untaped config set jira.base_url https://jira.example.com
untaped config set jira.token --prompt
untaped jira whoami
```

| Setting | Default | Purpose |
|---|---|---|
| `jira.base_url` | unset | Jira URL. |
| `jira.token` | unset | Personal access token. |
| `jira.token_command` | unset | Command (argv list) that prints the token when `jira.token` is unset, for example `'["op", "read", "op://work/jira/token"]'`. Without either, the `JIRA_API_TOKEN` environment variable is used. See [Tokens](../configuration.md#tokens). |
| `jira.assigned_jql` | `assignee = currentUser() AND resolution = Unresolved` | Base query for `issues assigned`. |
| `jira.default_project` | unset | Project for `issues create` without `--project`. |
| `jira.default_board_id` | unset | Board for `sprints list` without `--board-id`. |
| `jira.api_prefix`, `jira.agile_prefix` | `/rest/api/2`, `/rest/agile/1.0` | Change only if your Jira is mounted under another path. |
| `jira.page_size` | `50` | Results per API page. |
| `jira.confirm` | `destructive` | Which writes ask first: `always`, `destructive` or `never`. See [Change issues](#change-issues). |

## Find issues

```bash
untaped jira issues assigned
untaped jira issues assigned --status 'In Progress' --sprint 'openSprints()'
untaped jira issues search --project OPS --text "certificate" --limit 20
untaped jira issues search --jql 'project = OPS AND labels = infra ORDER BY created DESC'
untaped jira issues get OPS-123 OPS-124
untaped jira issues get OPS-123 --comments
untaped jira issues comments list OPS-123
```

- `issues assigned` always starts from `jira.assigned_jql`. `--jql` and the
  shortcut flags narrow it (ANDed). An `ORDER BY` in `--jql` replaces the
  default `updated DESC`.
- `issues search` with no query and no shortcut flags also uses
  `jira.assigned_jql`. For an unrestricted query pass `--jql`.
- `--assignee @me` means you. `--sprint` takes a sprint ID, a sprint name, or
  `openSprints()`, `futureSprints()`, `closedSprints()`.
- Search rows carry `key`, `summary`, `status`, `assignee`, `updated_at`,
  `url`, `api_url`, `issue_type` and `priority`. `issues get` adds `reporter`,
  `labels`, `created_at`, `resolution`, `description`, `links` and `comments`.
- `links` lists the issue's links (an empty list when there are none). Each
  link has the linked issue's `key`, `summary`, `status` and `url`, the link
  `type` name, the `direction` of this issue on it (`outward` when this issue
  is the link's source, `inward` when it is the target) and `relation`, the
  phrase Jira shows on this issue for that direction (`blocks`,
  `is blocked by`, `duplicates`, ...). Linked issues are not fetched; run
  `issues get` on their keys for more. The detail table shows one line per
  link: `blocks ABC-2 (To Do): Release 2.0`.
- The `issues search` table, and the `issues get` table for several issues,
  show `key`, `issue_type`, `status`, `priority`, `assignee`, `summary` and
  `updated_at`. `issues assigned` leaves out `assignee` (always you).
  `issues get` shows one issue as a detail view, leaving out empty fields.
  `--columns +name` or `--columns=-name` edits the table's columns; JSON and
  YAML keep every field.
- `issues get --comments` also fetches every comment. The table lists them
  after the issues (`author`, `created_at`, `body`, plus `issue_key` for
  several issues); JSON and YAML nest them under `comments` (otherwise
  `null`). `issues comments list KEY` prints only the comments, as
  `jira.comment` records (`id`, `issue_key`, `author`, `created_at`,
  `updated_at`, `body`, `api_url`); its table shows `author`, `created_at`
  and `body`.

## Change issues

`jira.confirm` picks which writes ask first:

| Value | Asks before |
|---|---|
| `destructive` (default) | Destructive writes only. |
| `always` | Every write. |
| `never` | No write. |

A write is **destructive** when it can replace or remove what an issue holds
now: `issues transition` (it changes the status and can set the resolution),
an `issues patch` that sets any field (`--summary`, `--description`, `--set`,
`--set-json`, `fields` in `--fields-file`), changes the assignee
(`--assignee`, `--unassign`), or has an `update` operation other than `add`
(`set`, `remove`, `edit`). `issues create`, `issues comment`, `issues links
create` and a patch whose `update` operations only `add` (for example a
label) only add, so they are sent without asking unless `jira.confirm` is
`always`.

Before asking, the write shows each REST request it will send and what it
changes, one line per field. A patch first reads the current values of the
fields it sets (none for an add-only patch) and names each old value the way
the new one does (`priority: 2 → 3` for `{"id": "3"}`); long text is cut to
60 characters for display but compared whole:

```text
PUT /rest/api/2/issue/OPS-123
  summary: "Rotate the API certificate" → "Rotate the API and web certificates"
  labels: + "tls"
PUT /rest/api/2/issue/OPS-123/assignee
  assignee: alice → bob
```

`--dry-run` shows the same preview on stderr, emits a `planned` outcome on
stdout and sends nothing, whatever `jira.confirm` says. Because the preview
reads the issue, `issues patch --dry-run` and `issues transition --dry-run`
need working credentials, and a patch dry run fails when the issue cannot
be read. `issues create`, `comment` and `links create` dry runs stay offline.
`--yes` skips the question and the preview (and its reads). Without a
terminal, a write that must ask exits 2 unless you pass `--yes` or
`--dry-run`.

```bash
untaped jira issues create --project OPS --issue-type Task \
  --summary "Rotate the API certificate" --description "Expires on 2026-10-01."

untaped jira issues patch OPS-123 --summary "Rotate the API and web certificates"
untaped jira issues patch OPS-123 --set-json 'labels=["infra","tls"]' --dry-run
untaped jira issues patch OPS-123 --assignee @me
untaped jira issues patch OPS-123 --unassign

untaped jira issues comment OPS-123 --body "Rotated on staging."
git log -1 --format=%B | untaped jira issues comment OPS-123 --yes
```

- `--assignee USER` assigns the issue (`@me` is you); `--unassign` clears the
  assignee. Both use Jira's dedicated `issue/KEY/assignee` endpoint, so they
  work even when the assignee field is not on the edit screen. With other
  field changes, the field edit is sent first, then the assignment. The flags
  override a `fields.assignee` in `--fields-file`.
- Issue keys must look like `PROJECT-123` and project keys like `PROJECT`
  (any case; sent uppercase), or be a numeric id; anything else exits 2
  before any request.
- `--set KEY=VALUE` sets a string field; `--set-json KEY=JSON` sets any field
  from JSON. Both repeat.
- `issues create --fields-file FILE` and `issues patch --fields-file FILE`
  start from a Jira-shaped YAML or JSON document (`fields` and `update`);
  flags override its fields.
- `issues comment` reads the body from `--body`, `--body-file`, or stdin.

### Transitions

```bash
untaped jira issues transitions OPS-123
untaped jira issues transition OPS-123 --to "Done"
untaped jira issues transition OPS-123 OPS-124 --id 31 --yes
untaped jira issues transition OPS-123 --to Done --resolution Fixed --comment "Shipped in 1.2."
```

`issues transitions KEY` lists `id`, `name` and `to_status`, the status each
transition leads to. Pass exactly one of `--to NAME` or `--id ID`. `--resolution NAME` sets the
resolution (many Done screens require one) and `--comment TEXT` adds a comment
in the same request. The preview names the transition, shows the status
change (`status: To Do → In Progress`) and the whole comment. It reads each
issue once; a transition picked by `--to` reuses the lookup, and an `--id`
the issue does not offer shows `(not available from this status)`. When an
issue cannot be read, its preview shows `(unknown)` instead of stopping the
batch. Several keys are transitioned in
one batch, shown as a table of `key`, `transition_id` and `action`; each
failed key prints `error: KEY: ...` and the command exits with the most
severe failure (see Limits).

Transition every issue of a search:

```bash
untaped jira issues search --project OPS --status 'In Review' --format pipe \
  | untaped jira issues transition --stdin --to Done --dry-run
```

### Links

```bash
untaped jira issues links create OPS-123 Blocks OPS-124 --dry-run
```

`links create KEY TYPE OTHER` reads as "KEY *outward phrase* OTHER": the
example makes OPS-123 block OPS-124. `TYPE` is the link type name (`Blocks`,
`Relates`, `Duplicate`, ...). The request sends KEY as `inwardIssue` and
OTHER as `outwardIssue`, the pairing under which Jira shows the outward phrase
on KEY. The preview and `--dry-run` print a `reads as:` line; check the
direction on one pair with `--dry-run` before linking in bulk.

## Projects, boards and sprints

```bash
untaped jira projects list
untaped jira projects get OPS
untaped jira boards list --project OPS --type scrum
untaped jira sprints list --board-id 42 --state active,future
```

Tables show projects as `key`, `name` and `project_type_key`, boards as
`id`, `name` and `type`, and sprints as `id`, `name`, `state`, `start_at`,
`end_at` and `goal` (hidden when no sprint has one). JSON and YAML also carry
a project's `id`, a board's `api_url` and a sprint's `origin_board_id`.

## Output

| Command | Record kind |
|---|---|
| `whoami` | `jira.user` |
| `issues get`, `issues search`, `issues assigned` | `jira.issue` |
| `issues create`, `patch`, `comment`, `transition`, `links create` | `jira.issue_outcome` (`action`: `created`, `updated`, `commented`, `transitioned`, `linked` or `planned`) |
| `issues comments list` | `jira.comment` |
| `issues transitions` | `jira.transition` |
| `projects list`, `projects get` | `jira.project` |
| `boards list` | `jira.board` |
| `sprints list` | `jira.sprint` |

`issues get --stdin` and `issues transition --stdin` read issue keys, or
`jira.issue` and `jira.issue_outcome` records.

## Limits

- Search requests are retried on HTTP 429 and 503. Writes are never retried.
- Usage mistakes exit 2: a blank `--jql`, both or neither of `--to`/`--id`,
  `sprints list` with no board, `--limit 0`.
- Failures exit by kind: 1 when the request itself failed (a missing issue,
  an invalid field, no matching transition), 4 when the environment needs
  fixing (a rejected token, a missing permission, bad `jira.*` settings), 5
  when Jira was unavailable (network, timeout, 5xx, 429), so retry later.
  With `--format json` (or `UNTAPED_DIAGNOSTICS=json`) stderr is JSON Lines
  whose errors carry `category`, `system` (`jira`), `retryable` and `hint`.

## See also

- [Pipes and record kinds](../reference/pipes.md)
- [Configuration reference](../reference/config.md#jira)
- [Exit codes](../reference/exit-codes.md)
