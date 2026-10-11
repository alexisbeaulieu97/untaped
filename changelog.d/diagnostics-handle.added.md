`ctx.diag` on `AppContext` and `PluginContext` emits typed stderr
diagnostics, a plugin's own kinds included; `untaped.testing`'s
`CliResult.diagnostics` lists what a command emitted.
