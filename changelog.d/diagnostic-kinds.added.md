Every stderr JSON line carries a `kind` (`untaped.message`, `untaped.failure`,
`untaped.debug`, `untaped.stale`, `untaped.progress`) whose fields
`untaped plugin schema <kind>` prints; ignore a `level` or `kind` you do not
know. ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600), [#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
