github detects whether its server is github.com or GitHub Enterprise Server,
and which release, and refuses a GitHub Enterprise Server older than 3.9 with
exit 4; `github.server_version` (`github.com` or `GitHub Enterprise Server
3.14.2`) sets it instead of detecting, and `untaped doctor` shows it in a
`github.server-api` row.
