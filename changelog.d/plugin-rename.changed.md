**Breaking (core):** "capability" is now "plugin" everywhere: `untaped
capabilities` is `untaped plugin list`, listing records of kind
`untaped.plugin`, and plugins are discovered from the `untaped.plugins`
entry-point group.
Quarantine reasons `reserved-root` and `profile-state-overlap` are now
`reserved-name` and `settings-state-overlap`; `duplicate-section` and
`state-shadow` are gone, since a plugin's section is its name.
