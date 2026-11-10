Tables, list and detail views, messages, errors and `--verbose` lines show
control characters escaped (`\x1b`), so a value someone else wrote, such as a
ticket summary or a server's error text, can no longer move the cursor,
retitle the terminal or write to the clipboard. `json`, `yaml`, `raw` and
`pipe` output keep values verbatim.
