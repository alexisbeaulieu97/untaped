# Jira

`untaped jira` searches, creates, updates, comments on and transitions Jira
issues, and looks up projects, boards and sprints. It targets Jira Data Center
and self-hosted Jira (REST API v2 and Agile 1.0), not Jira Cloud.

The [packaged skill](../../packages/untaped/src/untaped/capabilities/jira/skills/untaped-jira/SKILL.md)
and its references
([reading](../../packages/untaped/src/untaped/capabilities/jira/skills/untaped-jira/references/reading.md),
[writes](../../packages/untaped/src/untaped/capabilities/jira/skills/untaped-jira/references/writes.md))
hold the per-command detail; `--help` and `--columns ?` hold the options and
fields.

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
untaped jira issues assigned --status 'In Progress' --sprint 'openSprints()'
untaped jira issues search --jql 'project = OPS AND labels = infra ORDER BY created DESC'
untaped jira issues get OPS-123 --comments
```

`issues assigned` always starts from `jira.assigned_jql`, and its flags only
narrow it. `issues search` with no query and no shortcut flags uses
`jira.assigned_jql` too; pass `--jql` for an unrestricted query.

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

Assignment goes through its own request, so it works even when the assignee
field is not on the issue's edit screen.

`--dry-run` shows the same preview, prints a `planned` outcome and sends
nothing, whatever `jira.confirm` says. `--yes` skips the question. Without a
terminal, a write that must ask exits 2 unless you pass `--yes` or
`--dry-run`.

```bash
untaped jira issues patch OPS-123 --set-json 'labels=["infra","tls"]' --dry-run
git log -1 --format=%B | untaped jira issues comment OPS-123 --yes
```

### Transitions

Transition names depend on the issue's workflow and current status, so list
them with `issues transitions KEY` first, then pass `--to NAME` or `--id ID`.
To transition every issue of a search, pipe it and preview the batch:

```bash
untaped jira issues search --project OPS --status 'In Review' --format pipe \
  | untaped jira issues transition --stdin --to Done --dry-run
```

A batch continues past a failing key and exits with the most severe failure.

### Links

```bash
untaped jira issues links create OPS-123 Blocks OPS-124 --dry-run
```

`links create KEY TYPE OTHER` reads as "KEY *outward phrase* OTHER": the
example makes OPS-123 block OPS-124. The preview prints a `reads as:` line;
check the direction on one pair with `--dry-run` before linking in bulk.

## Output

Searches are retried on HTTP 429 and 503; writes are never retried. See
[Pipes and record kinds](../reference/pipes.md#jira) and
[Exit codes](../reference/exit-codes.md).

## See also

- [Configuration reference](../reference/config.md#jira)
- [Configuration](../configuration.md), for profiles and tokens
