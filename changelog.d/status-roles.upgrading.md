A plugin whose table coloured a status word of its own (`synced`, `ok`,
`stale`) marks the field with `Status(success=...)`, `warning=` or `error=`
for those words; core's outcome and check words need nothing. A command
that prints a view of a record as a `dict` builds each row with
`table_row(record, row)`, so the row keeps its record's colours.
