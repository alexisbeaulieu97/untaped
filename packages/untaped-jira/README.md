# untaped-jira

Install it as part of `untaped`: `uv tool install 'untaped[jira]'` or `pip install 'untaped[jira]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped jira` searches, creates, updates, comments on and transitions Jira
issues, and looks up projects, boards and sprints, from a terminal, a script
or an agent. It targets Jira Data Center and self-hosted Jira (REST API v2
and Agile 1.0), not Jira Cloud.

## Set up

Create a personal access token in Jira, then:

```bash
untaped config set jira.base_url https://jira.example.com
untaped config set jira.token --prompt
untaped jira whoami
```

To keep the token out of `config.yml`, use `jira.token_command` or
`JIRA_API_TOKEN`. Defaults such as `jira.default_project` and the
`jira.assigned_jql` base query are in the
[settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#jira).

## Find issues

```bash
untaped jira issues assigned --status 'In Progress' --sprint 'openSprints()'
untaped jira issues search --jql 'project = OPS AND labels = infra ORDER BY created DESC'
untaped jira issues get OPS-123 --comments
```

`issues assigned` narrows your `jira.assigned_jql` base query; pass `--jql`
to `issues search` for an unrestricted query.

## Change issues

```bash
untaped jira issues patch OPS-123 --assignee @me --set-json 'labels=["infra","tls"]' --dry-run
git log -1 --format=%B | untaped jira issues comment OPS-123 --yes
untaped jira issues links create OPS-123 Blocks OPS-124 --dry-run
```

The preview lists each request and one `field: old → new` line per change.
Writes preview and ask first according to `jira.confirm`; see the skill.

## Move issues through a workflow

```bash
untaped jira issues transitions OPS-123
untaped jira issues transition OPS-123 --to Done --dry-run
untaped jira issues search --project OPS --status 'In Review' --format pipe \
  | untaped jira issues transition --stdin --to Done --dry-run
```

Transition names depend on the issue's workflow and current status, so list
them first. A piped batch continues past a failing key.

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-jira/src/untaped_jira/skills/untaped-jira/SKILL.md) is the full reference: every workflow, safety rule and pitfall. Install it for your agent with `untaped skills install jira`; `untaped jira COMMAND --help` lists each command's options.

- [Records and exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#jira)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#jira)
