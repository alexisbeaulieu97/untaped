A script that pipes a comment into `untaped jira issues comment KEY` adds
`--body -`. One that sets `jira.base_url` sets `jira.server.url`. One that
passes `--yes` to `jira issues create`, `jira issues comment` or `jira issues
links create` drops it. One that set `jira.confirm: never` passes `--yes` to
`jira issues patch` and `jira issues transition`.
