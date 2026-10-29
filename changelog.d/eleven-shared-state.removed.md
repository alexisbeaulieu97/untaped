**Breaking (plugins):** 11.x shared state: `StateMap`, `StateCollection`,
`PluginSpec(state=)`, the spelled `StateRows("acme", "items", model=...,
key_field=..., remove_hint=...)` with its lazy `model=` function form,
`StateRows.per_project("acme", ...)`, and the `settings-state-overlap` and
`unlisted-state` quarantine reasons. `untaped doctor` drops its
"load state file" row, and `config set` no longer names a state key as
"managed by" its plugin: no state key sits in a settings section any more.
