**Breaking (users):** `untaped setup migrate-dirs` and doctor's
`migrate-dirs` row are gone, with the plugin hook behind them:
`PluginSpec.migrations`, `DirMigration`, `MigrationOptions`, `MigrationRow`,
`MigrationOutcome`, `delete_migration`, `move_migration`, `old_dirs`,
`other_layouts_migration` and `unsafe_dir`. A `Store` no longer takes
`bare=` (11.0's file without a layout in its name), so ansible no longer
renames 11.0's `index.sqlite3` to its layout's name and builds its index
anew; and the experimental `copy_data_file` leaves `untaped.sdk`. 12.0
refuses an 11.x home, so it has nothing to move.
