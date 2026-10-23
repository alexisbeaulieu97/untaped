A 12.0 plugin class names its `Cache`, `DoctorCheck` and `DirMigration` by
the attribute holding it: `motd = Cache(label=..., max_age=...)` is the
plugin's cache `motd`, and `repo_store = DoctorCheck(title=..., run=...)` is
the check `<plugin>.repo-store`. The plugin's name comes from the run, so
these blocks are read inside one; the 11.x spelling, with the plugin and name
or the id first, keeps working.
