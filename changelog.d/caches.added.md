`Cache` in `untaped.sdk` keeps data a plugin fetched from an upstream:
`serve()` refreshes it once it is older than its max age, failing fast when
offline, and says when it serves stale data; `refresh_option` adds `--refresh`
and `--cached`. Each cache listed on `PluginSpec.caches` gets the setting
`<plugin>.caches.<cache>.max_age_seconds`, and `Cache(renamed_from=)` moves an
old key into it. `Cache(version=)`, usually with `code_version()`, refreshes
data made by another version of the code that fills it, and the stale line's
`outdated` says so.
