Scripts that ran `untaped capabilities` run `untaped plugin list` and read
kind `untaped.plugin`. A third-party plugin moves its entry point to the
`untaped.plugins` group and builds a `PluginSpec` named after its
distribution (`untaped-<name>`), passing `settings=` and `state=` and no
`config_section`; a doctor check reads `PluginContext.settings_fields`
instead of `profile_fields`.
