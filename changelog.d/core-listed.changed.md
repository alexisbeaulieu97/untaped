**Breaking (users):** `untaped plugin list` (and `installed_plugins()` and a
command's `ctx.plugins`) lists untaped itself as the plugin `untaped`, with
untaped's version, marked by the new `root` field of
`untaped.installed_plugin` records: `true` for it, `false` for every plugin
installed into untaped.
