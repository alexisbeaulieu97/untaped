`untaped.testing.check_conventions` checks more: stability marks, setting
names (`settings-naming`), `echo("failed: …")` and `warnings.warn()` (report
a per-item failure with `report_error(exc, item=…)` and warn with
`ui.message("warning", …)`), and a plugin's `prompt_toolkit` imports
(`terminal-boundary`: build the interface with `untaped.sdk` screens, or
waive a line with `# untaped: allow terminal-boundary`). It fails with the
reason when the capability is quarantined, a broken rename declaration
included.
([#464](https://github.com/alexisbeaulieu97/untaped/pull/464),
[#492](https://github.com/alexisbeaulieu97/untaped/pull/492),
[#518](https://github.com/alexisbeaulieu97/untaped/pull/518),
[#525](https://github.com/alexisbeaulieu97/untaped/pull/525))
