A warning untaped prints while it dispatches a command, before the command
parses (a deprecated command's notice, say), follows a `--format` given on
the command line or in `UNTAPED_FORMAT`: under `--format json` it is a JSON
line on stderr, no longer a text line among them. The `untaped.ui.format`
setting doesn't reach it yet.
