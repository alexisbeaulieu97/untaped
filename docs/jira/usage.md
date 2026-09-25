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
| `jira.token_command` | unset | Command (argv list) that prints the token when `jira.token` is unset, for example `'["op", "read", "op://work/jira/token"]'`. See [Tokens](../configuration.md#tokens). |
| `jira.assigned_jql` | `assignee = currentUser() AND resolution = Unresolved` | Base query for `issues assigned`. |
| `jira.default_project` | unset | Project for `issues create` without `--project`. |
| `jira.default_board_id` | unset | Board for `sprints list` without `--board-id`. |
| `jira.api_prefix`, `jira.agile_prefix` | `/rest/api/2`, `/rest/agile/1.0` | Change only if your Jira is mounted under another path. |
| `jira.page_size` | `50` | Results per API page. |

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
  `url` and `api_url`. `issues get` adds `issue_type`, `priority`, `reporter`,
  `labels`, `created_at`, `resolution`, `description`, `links` and `comments`.
- `links` lists the issue's links (an empty list when there are none). Each
  link has the linked issue's `key`, `summary`, `status` and `url`, the link
  `type` name, the `direction` of this issue on it (`outward` when this issue
  is the link's source, `inward` when it is the target) and `relation`, the
  phrase Jira shows on this issue for that direction (`blocks`,
  `is blocked by`, `duplicates`, ...). Linked issues are not fetched; run
  `issues get` on their keys for more. The detail table shows one line per
  link: `blocks ABC-2 (To Do): Release 2.0`.
- The `issues get` table shows one issue as a detail view and several issues
  as a compact table (`key`, `issue_type`, `status`, `priority`, `assignee`,
  `summary`, `updated_at`); `--columns` picks others.
- `issues get --comments` also fetches every comment. The table lists them
  after the issues; JSON and YAML nest them under `comments` (otherwise
  `null`). `issues comments list KEY` prints only the comments, as
  `jira.comment` records (`id`, `issue_key`, `author`, `created_at`,
  `updated_at`, `body`, `api_url`).

## Change issues

Every write shows the REST request it will send and asks first. `--dry-run`
shows it and sends nothing; `--yes` skips the question. Without a terminal,
pass `--yes` or `--dry-run` (exit 2 otherwise).

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
  assignee.
- `--set KEY=VALUE` sets a string field; `--set-json KEY=JSON` sets any field
  from JSON. Both repeat.
- `issues create --template FILE` and `issues patch --body-file FILE` start
  from a Jira-shaped YAML or JSON payload; flags override its fields.
- `issues comment` reads the body from `--body`, `--body-file`, or stdin.

### Transitions

```bash
untaped jira issues transitions OPS-123
untaped jira issues transition OPS-123 --to "Done"
untaped jira issues transition OPS-123 OPS-124 --id 31 --yes
untaped jira issues transition OPS-123 --to Done --resolution Fixed --comment "Shipped in 1.2."
```

Pass exactly one of `--to NAME` or `--id ID`. `--resolution NAME` sets the
resolution (many Done screens require one) and `--comment TEXT` adds a comment
in the same request. Several keys are transitioned in
one batch; each failed key prints `error: KEY: ...` and the command exits 1.

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
direction on one pair before linking in bulk.

## Projects, boards and sprints

```bash
untaped jira projects list
untaped jira projects get OPS
untaped jira boards list --project OPS --type scrum
untaped jira sprints list --board-id 42 --state active,future
```

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

## See also

- [Pipes and record kinds](../reference/pipes.md)
- [Configuration reference](../reference/config.md#jira)
- [Exit codes](../reference/exit-codes.md)
