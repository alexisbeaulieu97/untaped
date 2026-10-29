**Breaking (plugin authors):** a table no longer colours a cell because its
column is named `status`, `action`, `result`, `state` or `job_status`: it
colours the words its field's `Status` names, and core's outcome and check
records name theirs. A row printed as a plain
`dict` (through `emit` or `render_rows`) has no fields, so its words are no
longer coloured, and free-text fields such as a jira issue's `status` stay
plain.
