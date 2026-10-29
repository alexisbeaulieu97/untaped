**Breaking (plugins):** the 11.x plugin-spec names leave `untaped.sdk`:
`PluginSpec`, `SkillAsset`, `plugin_dir`, `committed_path`, `AppContext`,
`app_context`, `deprecated_alias`, `DEPRECATED_OPTIONS` and
`deprecated_behaviour`, and `Project.plugin_dir(spec)` goes too. `untaped
lint` drops its `foreign-section` rule, which read
`app_context().section(...)`.
