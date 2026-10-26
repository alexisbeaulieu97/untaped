**Breaking (users):** the setting `jira.base_url`, deprecated in 11.x for
`jira.server.url`, is retired: it is no longer read, doctor names its
successor, and `untaped config migrate` moves it there. The setting
`jira.confirm` is retired too: jira asks before writes that replace or
remove, and `--yes` skips the question; doctor names it and
`untaped config migrate` deletes it. `jira issues create`, `jira issues
comment` and `jira issues links create` only add, so they never ask and no
longer take `--yes`; passing it exits 2. `--dry-run` still previews them.
