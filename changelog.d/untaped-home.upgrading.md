Replace `UNTAPED_CONFIG=/some/dir/config.yml` with `UNTAPED_HOME=/some/dir`,
and move a config file with another name to `config.yml` in its own folder.
`UNTAPED_STATE` has no replacement: state lives beside the config. Tests built
on `untaped.sdk.testing` no longer see `UNTAPED_CONFIG`; `home.path` is now
the untaped home (it was `HOME`), so `home.path / ".untaped" / "x"` becomes
`home.path / "x"`.
