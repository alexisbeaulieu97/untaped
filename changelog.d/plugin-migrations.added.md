Plugins declare their own `setup migrate-dirs` rows in
`PluginSpec.migrations`: `DirMigration`, `MigrationRow`, `MigrationOutcome`
(kind `untaped.migration`) and `MigrationOptions`, with `delete_migration`,
`old_dirs`, `dir_bytes`, `unsafe_dir`, `retired_values` and `shown_path` in
the SDK; `untaped plugin check` runs each preview on an empty `HOME`.
