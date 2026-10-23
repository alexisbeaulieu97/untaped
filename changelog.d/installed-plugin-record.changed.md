**Breaking (plugins):** the `untaped plugin list` record class is now
`untaped.sdk.InstalledPlugin` (its kind stays `untaped.plugin`), since
`untaped.sdk.Plugin` is the plugin base class. `untaped.sdk.Key` is now the
record mark; the screen kit's key message is `untaped.screen.core.Key`.
