**Breaking (users):** an `untaped ansible` source is the repos one plugin
selects: `source set NAME --stdin` saves the selector a plugin prints
(`untaped github repos selector --team acme/platform --format pipe`), and
`source set NAME --from PLUGIN --match 'infra/*'` takes the repos a plugin
lists whose full name matches. Each refresh asks the source's plugin for its
repos again, at the URLs it gives, so `github.git_protocol` decides how
GitHub repos are fetched. `source list` and `source get` rows carry `source`
(the plugin), `selects` (the selector in words) and `selector` in place of
`orgs`, `teams` and `repos`. `source set`, `source list` and `source get` are
experimental while the git plugin's `RepoSelector` is.
