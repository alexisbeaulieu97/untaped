A script that treated a run printing `error:` lines as a success because it
exited 0 now sees exit 1 or the failure's own code; a plugin whose output
should not count prints it with `ctx.diag.relay(...)` or as a warning.
([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
