Errors name their system with `System` (`untaped`, `local`, `git`,
`http`) or, for a plugin's own service, `Service(Settings)`, which follows
the plugin's name: awx, github and jira errors read the name the plugin
runs under. `untaped lint`'s own errors now name `local`, not `lint`.
