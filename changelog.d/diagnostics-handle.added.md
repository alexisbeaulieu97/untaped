`ctx.diag` on `AppContext` and `PluginContext` emits typed stderr
diagnostics, a plugin's own kinds included, and `ctx.diag.relay` prints
another tool's output without counting it; `untaped.testing`'s
`CliResult.diagnostics` lists what a command emitted.
