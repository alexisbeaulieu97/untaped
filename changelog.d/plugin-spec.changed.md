**Breaking (sdk):** `CapabilitySpec` is `PluginSpec`, `CapabilityContext`
is `PluginContext` with only `settings`, and `CapabilityProvider` is gone.
A plugin's config section and command group are its name, so
`config_section` is gone; `profile_model` and `state_model` are `settings`
and `state`, and they and `app_factory` are optional. A name is lowercase
words joined by hyphens, and a name core keeps for itself is quarantined as
`reserved-name`.
