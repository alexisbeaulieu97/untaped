`ctx.diag.info(rows)` prints records on stderr as one table, laid out like a
stdout table: their `Column` fields, in the run's theme, with empty columns
left out and the list view where the theme asks for it. A `Confirm` or
`DryRun` preview can return rows the same way (`untaped.sdk.PreviewItem`).
Under `-q` the table is muted unless `unmuted=True`; JSON diagnostics carry
it as plain `key: value` text.
