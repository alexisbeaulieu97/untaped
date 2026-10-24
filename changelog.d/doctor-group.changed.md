`untaped doctor` and `doctor fix` are now built on core's own plugin class.
Rows, kinds, exit codes and the checklist are unchanged. `doctor fix` asks
like core's other groups: the prompt reads `Run N automatic fixes?` after the
plan, off a terminal without `--yes` it exits 2 naming that question, and
agent pages tag it `(asks first)`. Under `--format json`, `yaml`, `pipe` or
`raw` the summary and hint lines on stderr now come before the rows on
stdout.
