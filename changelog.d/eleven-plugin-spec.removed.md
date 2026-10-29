**Breaking (plugins):** an entry point naming an 11.x `PluginSpec` no
longer composes: it is quarantined (`not-a-plugin-class`, which replaces
`not-a-spec`) with a line naming the class to name instead. Also the
`provides=` parameter of `untaped.sdk.testing.compose_with`.
