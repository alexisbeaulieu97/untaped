Define a shell alias or function instead (`alias failed='untaped awx jobs
list --status failed'`), copying each one from `shell.aliases` in
`config.yml`. `untaped doctor` names a `shell.aliases` key left there, and
`untaped config migrate` deletes it.
