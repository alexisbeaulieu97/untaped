Screens (experimental): input components (`TextInput`, `SecretInput`,
`Select`, `Tabs`, ...) and `field_for`, which maps a setting's type to its
component, enforcing the setting's bounds and description; `ui.color_roles`
gains `screen.caret` and `screen.emphasis`. The settings walker's
descriptors gain `metadata`, `optional` and `description` (additive;
`untaped.config_schema` is internal, but `field_for` takes the descriptor).
([#526](https://github.com/alexisbeaulieu97/untaped/pull/526))
