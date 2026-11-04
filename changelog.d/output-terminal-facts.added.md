`Output` tells a command two facts about its run (experimental):
`format_chosen`, whether the run named its format (`--format`,
`UNTAPED_FORMAT` or `untaped.ui.format`), and `to_terminal`, whether stdout
is a terminal. A command that opens a screen instead of rows checks both, as
a pager does, so a pipe or a named format still gets rows. Tests fake a
terminal on stdout with `invoke_cli(..., stdout_terminal=True)` or
`Install.cli(..., stdout_terminal=True)`.
