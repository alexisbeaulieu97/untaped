**Breaking (users):** `untaped plugin list --contracts` is now
`untaped plugin contracts`, and the records `plugin list` and `plugin
contracts` print are `untaped.installed_plugin` and `untaped.contract_method`
(were `untaped.plugin` and `untaped.contract`): a record's kind is its class
name. Core composes its own commands first, as a plugin of itself, so a
plugin named `untaped`, after a root command or after a config key is
quarantined as `duplicate-name` (was `reserved-name`); the names nothing
holds any more (`core`, `sdk`, `contracts`, `plugins`, `default`, `state`)
are free.
