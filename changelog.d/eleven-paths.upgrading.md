Log in again with `untaped auth login <plugin>` for awx and jira: a token in
`awx.token`, `awx.token_command`, `jira.token` or `jira.token_command` is no
longer read. Replace `untaped.http.verify_ssl: false` with `verify_tls: false`
on each server that needs it.
