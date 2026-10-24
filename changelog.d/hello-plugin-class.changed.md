The example plugin `examples/untaped-hello` is a 12.0 plugin class: `Hello`
in `untaped_hello.plugin`, its commands as classes, its cache, migration and
stub skill as attributes. A doctor check's or migration's `PluginContext` has
`plugin` (its folder as `ctx.plugin.dir`), and a migration's outcomes may
leave `id` out for core to stamp.
