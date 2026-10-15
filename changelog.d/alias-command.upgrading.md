`untaped config migrate` deletes `shell.aliases` without printing the
aliases it held, so copy each one from `config.yml` (in every profile) into
a shell alias or function first
(`alias failed='untaped awx jobs list --status failed'`). `untaped doctor`
names a `shell.aliases` key left there.
