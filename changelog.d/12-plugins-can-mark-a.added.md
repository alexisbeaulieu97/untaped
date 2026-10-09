Plugins can mark a command, group or whole capability `experimental` or
`deprecated(replacement=…)` once (`@experimental`, `create_app(stability=…)`,
`CapabilitySpec(stability=…)`). Experimental ones sit in an Experimental
panel in `--help` and end their help with a line saying so, instead of
saying it in their one-line summary (`workspace` and `awx test` move there);
deprecated ones warn once per run and leave the `--help` listing and shell
completion, and the new `untaped --deprecated --help` lists them.
([#518](https://github.com/alexisbeaulieu97/untaped/pull/518))
