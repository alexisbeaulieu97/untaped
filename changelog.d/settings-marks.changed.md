`untaped doctor` shows one `setup` row per service in place of the
`awx.connection`, `github.connection` and `jira.connection` rows: `not set up`
for a service you don't use, what is still missing with the command that sets
it, or where its token comes from. `doctor --online` contacts a service only
once all of it is set up, and a failure that is not a rejected token or an
untrusted certificate shows the settings it was set up with. `setup` and
`setup plan` read what each plugin marks, so a step's placeholder is the
setting's name, as in `<URL>`, and `setup plan` no longer lists a
`github.server.url` step: its default URL needs nothing set up.
