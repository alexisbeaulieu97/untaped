Hold each block on your plugin class and leave its plugin and name out:
`motd = Cache(label=..., max_age=...)`, `token = Credential(key=...)`,
`repo_store = DoctorCheck(title=..., run=...)`. A store is a numbered place,
`index = Dir(file="index.sqlite3", layout=2)`, which lives in
`plugins/<plugin>/index/` (an 11.x store's files are not read), and a cache
that describes it follows it with `Cache(..., follows=index)`. A
`Refresh(CACHE)` field replaces `refresh_option`, and `ExecutableCheck` and
`OnlineCheck` on the plugin class replace `executable_check` and
`online_check`. Data a setting points at, which `setting_guard` kept from
cleanup, is a place at that setting: `Dir(at=key(Settings).index_dir,
layout=2)`.
