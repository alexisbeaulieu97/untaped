The example plugin `examples/untaped-hello` is a 12.0 plugin class: `Hello`
in `untaped_hello.plugin`, its commands as classes, its cache and stub skill
as attributes; its errors name `System.LOCAL` instead of `hello`. A doctor
check's `PluginContext` takes `owner=` (core sets it) and has `plugin`, so
the body reaches its folder as `ctx.plugin.dir`.
