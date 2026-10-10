**Breaking (sdk):** a plugin's entry point names its `PluginSpec` constant
(`acme = "untaped_acme:SPEC"`). Anything else, a function returning a spec
included, is quarantined as `not-a-spec` and never called, so `provider()`
is gone from every plugin. `untaped.testing.provider_candidate` is
`plugin_candidate`.
