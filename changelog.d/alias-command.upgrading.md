Before running `untaped config migrate`, which deletes `shell.aliases`
without printing the aliases it held, copy each one from `shell.aliases` in
`config.yml` (in every profile) into a shell alias or function
(`alias failed='untaped awx jobs list --status failed'`). `untaped doctor`
names a `shell.aliases` key left there.
