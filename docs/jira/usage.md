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
  `labels`, `created_at`, `resolution` and `description`.

## Change issues

Every write shows the REST request it will send and asks first. `--dry-run`
shows it and sends nothing; `--yes` skips the question. Without a terminal,
pass `--yes` or `--dry-run` (exit 2 otherwise).

```bash
untaped jira issues create --project OPS --issue-type Task \
  --summary "Rotate the API certificate" --description "Expires on 2026-10-01."

untaped jira issues patch OPS-123 --summary "Rotate the API and web certificates"
untaped jira issues patch OPS-123 --set-json 'labels=["infra","tls"]' --dry-run

untaped jira issues comment OPS-123 --body "Rotated on staging."
git log -1 --format=%B | untaped jira issues comment OPS-123 --yes
```

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
```

Pass exactly one of `--to NAME` or `--id ID`. Several keys are transitioned in
one batch; each failed key prints `error: KEY: ...` and the command exits 1.

Transition every issue of a search:

```bash
untaped jira issues search --project OPS --status 'In Review' --format pipe \
  | untaped jira issues transition --stdin --to Done --dry-run
```

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
| `issues create`, `patch`, `comment`, `transition` | `jira.issue_outcome` (`action`: `created`, `updated`, `commented`, `transitioned` or `planned`) |
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
