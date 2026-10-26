A command's stderr keeps one format from its first line to its last: a warning
untaped prints before the command parses (a quarantined plugin, a deprecated
command's notice, a busy references lock) follows the command's format as its
own lines do, so under JSON output it is a JSON line, not a text line among
them, and under text output it uses the `untaped.ui.theme`. `UNTAPED_FORMAT`
and `untaped.ui.format` set that format only for a command whose `--format`
defaults to `table`; one with another default (`profile show`, YAML) or no
`--format` keeps text on stderr unless the line gives `--format`, so
`UNTAPED_FORMAT=json untaped profile show --bogus` now reports in text where
11.1 printed a JSON line. A line naming no command (root `--help`,
`--version`, an unknown command) follows `--format`, then `UNTAPED_FORMAT`,
then `untaped.ui.format`. An error in a root option (a malformed `--profile`,
`-v -q`) follows only `--format` and `UNTAPED_FORMAT`. An invalid
`UNTAPED_FORMAT` under a command that reads it exits 2 before anything else
runs, quarantine warnings included. A command a plugin still mounts the 11.x
way keeps the behaviour it had.
