A plugin can deprecate a behaviour, not only a command or a setting:
`deprecated_behaviour("row-kind", removed_in=3, replacement=...)` listed in
the new `PluginSpec(deprecations=...)` gets a `behaviour acme.row-kind` line
in the public surface, and its `notify`, `warn` and `sentence` build the
warning, named after the plugin's distribution and dated like every other
deprecation
([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
