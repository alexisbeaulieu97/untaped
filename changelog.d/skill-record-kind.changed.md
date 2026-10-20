The `untaped.skill` record (`skills list`) gains `kind` (`stub` or `copy`),
and so does `untaped.installed_skill` (`skills status`). A stub's `source`
is `untaped:stub/1`, the template's identity, not a path: read `kind` before
treating `source` as a directory. `skills status` reports two new states,
`newer` and `edited`.
