Plugins can mark a command, group or whole plugin `experimental` or
`deprecated(replacement=…)` once (`@experimental`, `create_app(stability=…)`,
`PluginSpec(stability=…)`). Experimental ones sit in an Experimental
panel in `--help` and end their help with a line saying so; deprecated ones
are listed by the new `untaped --deprecated --help` and warn once per run.
`check_conventions` checks the marks.
([#518](https://github.com/alexisbeaulieu97/untaped/pull/518))
