A plugin can let projects hold its items with an `ItemStore` in
`PluginSpec.stores` (experimental, with `ReleaseAsk`, `MoveAsk`, `LetGo` and
`Refused` in `untaped.sdk` and test helpers in `untaped.testing`), and
untaped asks it how to release them when a project goes and to repair them
when one moves:
[projects.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/projects.md#holding-a-plugins-items).
