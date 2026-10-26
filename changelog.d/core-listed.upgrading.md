A script or plugin that wants only the plugins installed into untaped from
`untaped plugin list --format json`, `installed_plugins()` or `ctx.plugins`
skips the row whose `root` is true.
