A 12.0 plugin class names its `Cache`, `Credential`, `DoctorCheck`,
`DirMigration` and `DeprecatedBehaviour` by the attribute holding it:
`motd = Cache(label=..., max_age=...)` is the plugin's cache `motd`, and
`repo_store = DoctorCheck(title=..., run=...)` is the check
`<plugin>.repo-store`. `ExecutableCheck("git", purpose=...)` and
`OnlineCheck(probe=...)` are the program-on-PATH and online checks in that
form. The plugin's name comes from the run, so these blocks are read inside
one; the 11.x spelling, with the plugin and name or the id first, keeps
working.
