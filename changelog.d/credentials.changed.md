`awx`, `jira` and `github` take `server: {url, verify_tls}`: `base_url` is
now `server.url` (the old key and its variable still read, with a warning,
and `untaped config migrate` renames them), and `verify_tls` is that server's
certificate check (`true`, a CA file, or `false`). `untaped config migrate`
also files each profile's `token` and `token_command` as stored logins and
removes them from `config.yml`, after a confirmation (`--yes` off a
terminal); a line it can't move stays, listed as kept. git's credential
helper line in untaped's worktrees now carries a nonce for its worktree, so
a call by hand gets nothing.
