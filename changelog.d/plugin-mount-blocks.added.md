A 12.0 plugin class's caches, directory migrations and doctor checks reach
`untaped config`, `setup migrate-dirs` and `doctor` under the names its
attributes give them (`motd = Cache(...)` is
`hello.caches.motd.max_age_seconds`), and `untaped plugin schema` prints the
record kinds its declaration names.
