# Jira

`untaped jira` searches, creates, updates, comments on and transitions Jira
issues, and looks up projects, boards and sprints. It targets Jira Data Center
and self-hosted Jira (REST API v2 and Agile 1.0), not Jira Cloud.

The [packaged skill](../../src/untaped/capabilities/jira/skills/untaped-jira/SKILL.md)
is the full reference for fields, previews and edge cases.

## Set up

Create a personal access token in Jira, then:

```bash
untaped config set jira.base_url https://jira.example.com
untaped config set jira.token --prompt
untaped jira whoami
```

To keep the token out of `config.yml`, set `jira.token_command` to a command
that prints it (for example `'["op", "read", "op://work/jira/token"]'`), or
export `JIRA_API_TOKEN`; see [Tokens](../configuration.md#tokens). Defaults
such as `jira.default_project`, `jira.default_board_id` and the
`jira.assigned_jql` base query are in the
[configuration reference](../reference/config.md#jira).

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
- `issues get` adds detail fields to what a search shows, including the
  issue's `links` (with the phrase Jira shows, such as `blocks` or
  `is blocked by`). `--comments` also fetches every comment.

## Change issues

`jira.confirm` picks which writes ask first:

| Value | Asks before |
|---|---|
| `destructive` (default) | Destructive writes only. |
| `always` | Every write. |
| `never` | No write. |

A write is **destructive** when it can replace or remove what an issue holds
now: `issues transition`, or an `issues patch` that sets a field, changes the
assignee, or has an `update` operation other than `add`. Creating an issue,
commenting, linking, and a patch that only adds (a label, say) are sent
without asking unless `jira.confirm` is `always`.

Before asking, the write shows each REST request it will send and what it
changes, one line per field:

```text
PUT /rest/api/2/issue/OPS-123
  summary: "Rotate the API certificate" → "Rotate the API and web certificates"
  labels: + "tls"
PUT /rest/api/2/issue/OPS-123/assignee
  assignee: alice → bob
```

`--dry-run` shows the same preview, prints a `planned` outcome and sends
nothing, whatever `jira.confirm` says. `--yes` skips the question. Without a
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

`--set KEY=VALUE` sets a string field and `--set-json KEY=JSON` any field;
`--fields-file FILE` starts from a Jira-shaped YAML or JSON document
(`fields` and `update`). `issues comment` reads the body from `--body`,
`--body-file`, or stdin.

### Transitions

```bash
untaped jira issues transitions OPS-123
untaped jira issues transition OPS-123 --to "Done"
untaped jira issues transition OPS-123 OPS-124 --id 31 --yes
untaped jira issues transition OPS-123 --to Done --resolution Fixed --comment "Shipped in 1.2."
```

`issues transitions KEY` lists the transitions an issue offers and the
status each leads to. Pass exactly one of `--to NAME` or `--id ID`;
`--resolution` and `--comment` go in the same request. Several keys are
transitioned in one batch; each failed key prints `error: KEY: ...` and
the command exits with the most severe failure.

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
`Relates`, `Duplicate`, ...). The preview prints a `reads as:` line; check the
direction on one pair with `--dry-run` before linking in bulk.

## Projects, boards and sprints

```bash
untaped jira projects list
untaped jira projects get OPS
untaped jira boards list --project OPS --type scrum
untaped jira sprints list --board-id 42 --state active,future
```

## Output

`issues get --stdin` and `issues transition --stdin` read issue keys or the
issue records of another `jira` command. Searches are retried on HTTP 429 and
503; writes are never retried. See
[Pipes and record kinds](../reference/pipes.md#jira) and
[Exit codes](../reference/exit-codes.md).

## See also

- [Configuration reference](../reference/config.md#jira)
- [Configuration](../configuration.md), for profiles and tokens
