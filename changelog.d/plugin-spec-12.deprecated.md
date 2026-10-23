For plugin authors: 12.0 replaces `PluginSpec` with a class each plugin
defines, and its commands become classes too; Cyclopts leaves `untaped.sdk`.
`create_app`, `@writes`, the `*Option` aliases, `get_config_section`,
`read_identifiers`, `emit` from command code, `SkillAsset`, `provides=`,
`guides=`, `table_columns`, `refresh_option`, `report_errors`,
`plugin_dir(SPEC)` and plain-string `kind=` and `system=` arguments go with
it. 11.1 prints no warning for these, since our own plugins still use them; a
plugin built on `PluginSpec` stops loading in 12.0.
