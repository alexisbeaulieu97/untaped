A plugin can declare a `Store` (experimental): a file or folder it keeps
under a name per layout, such as `index.v4.sqlite3`, so installs of
different untaped versions that share `~/.untaped` never open each other's
data. `untaped doctor` gains a `stores` row naming each store's file, any
other layout left on disk, what holds each layout and why one is kept, and
`untaped.testing.assert_schema_pinned` fails a SQLite store's tests when its
schema changes without the right version bump.
