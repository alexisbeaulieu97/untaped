A plugin marks what it needs set up on its settings model: `NeedsSetup` on an
`X | None = None` field it can't run without, and `Secret(env=...,
suggest=Suggest(...))` on a top-level `SecretStr | None` one. `untaped setup`,
`setup plan`, `doctor` and the token lookup read the marks, and untaped adds
each secret's `<field>_command` setting; a misplaced mark quarantines the
plugin
([plugins.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/plugins.md#settings-and-the-plugin-app)).
