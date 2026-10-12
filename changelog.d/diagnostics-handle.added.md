`ctx.diag` on `AppContext` and `PluginContext` emits typed stderr diagnostics,
a plugin's own kinds included, `ctx.diag.info(…, unmuted=True)` prints a
preview, a stream or output a flag asks for under `-q`, `run_hint()` builds a
hint's body, and `ctx.diag.relay` prints another tool's output without
counting it; `untaped.testing`'s `CliResult.diagnostics` lists what a command
emitted.
([#600](https://github.com/alexisbeaulieu97/untaped/pull/600),
[#622](https://github.com/alexisbeaulieu97/untaped/pull/622))
