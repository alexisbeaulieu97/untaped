Tables, list and detail views, messages, errors, relayed job and hook logs
and `--verbose` lines show control characters escaped (`\x1b`), so a value
someone else wrote, such as a ticket summary, a task name or a server's error
text, can no longer move the cursor, retitle the terminal or write to the
clipboard. A relayed log keeps its colours. `json`, `yaml`, `raw` and `pipe`
output, and a log or document a command prints on stdout, keep values
verbatim.
