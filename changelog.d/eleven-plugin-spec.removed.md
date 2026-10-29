**Breaking (plugins):** an entry point naming an 11.x `PluginSpec` no
longer composes: it is quarantined (`not-a-plugin-class`, which replaces
`not-a-spec`) with a line naming the class to name instead. Also, in
`untaped.sdk.testing`, the `provides=` parameter of `compose_with` and the
`PluginSpec`s that `compose_with`, `plugin_candidate` and `assert_surface`
took; `plugin_candidate` takes a plugin class alone, without
`distribution=` or `requires_dist=`.
