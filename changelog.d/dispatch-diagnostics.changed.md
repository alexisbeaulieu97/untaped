A command's stderr keeps one format from its first line to its last: a
warning untaped prints before the command parses (a quarantined plugin, a
deprecated command's notice, a busy references lock) follows the command's
`--format`, `UNTAPED_FORMAT` or `untaped.ui.format` setting as its own lines
do, so under JSON output it is a JSON line, not a text line among them, and
under text output it uses the `untaped.ui.theme`. `UNTAPED_FORMAT` and
`untaped.ui.format` count only for a command whose default format is `table`:
`UNTAPED_FORMAT=json untaped profile show` (YAML by default) keeps text on
stderr throughout. A command a plugin still mounts the 11.x way keeps the
behaviour it had.
