`untaped dotfiles show` with no selector at a terminal opens a screen to
browse every package: `space` turns a package or item on or off, `p`
cycles its policy, `e` answers a plain input, and `ctrl-s` saves every
change to `config.yml` at once, said line by line after it closes. esc
closes it saving nothing. A named format (`--format json`, or
`UNTAPED_FORMAT` or `untaped.ui.format` set), `--columns`, or a stdout or
stdin that isn't a terminal (`show | grep starship`) prints the rows as
before.
