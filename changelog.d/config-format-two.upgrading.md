Start `config.yml` again for 12.0 (`untaped setup` writes a format 2 file),
or rewrite it by hand: add `format_version: 2` at the top, move the
`profiles.default` sections to the top level, keep the other profiles under
`profiles:`, and replace `active: NAME` with `export UNTAPED_PROFILE=NAME` in
your shell.
