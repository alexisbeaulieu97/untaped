**Breaking (plugins):** the directory migration hook is gone:
`PluginSpec.migrations`, `DirMigration`, `MigrationOptions`, `MigrationRow`,
`MigrationOutcome`, `delete_migration`, `move_migration`, `old_dirs`,
`other_layouts_migration` and `unsafe_dir`. A `Store` no longer takes
`bare=` (11.0's file without a layout in its name), and the experimental
`copy_data_file` leaves `untaped.sdk`.
