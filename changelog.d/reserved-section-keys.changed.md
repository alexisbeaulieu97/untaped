**Breaking (sdk):** A plugin whose settings or state model has a field named
`extensions` or `caches` is quarantined (`bad-settings-keys`): untaped adds
those keys to plugin sections. `caches` is a reserved plugin name too.
