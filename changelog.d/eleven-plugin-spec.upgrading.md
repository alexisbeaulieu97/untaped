Move the plugin to a 12.0 plugin class in a leaf `plugin.py` and point its
entry point at it (`acme = "untaped_acme.plugin:Acme"`), as the quarantine
line says; a module that no longer imports under 12.0 is quarantined as
`malformed-entry-point` first, naming the import that failed. A test that
gave `compose_with` a fake through `provides=` composes a plugin class whose
`fills` holds the fake provider, and one that passed a `PluginSpec` to
`plugin_candidate` or `assert_surface` passes its plugin class.
