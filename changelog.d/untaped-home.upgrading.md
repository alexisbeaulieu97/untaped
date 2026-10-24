Replace `UNTAPED_CONFIG=/some/dir/config.yml` with `UNTAPED_HOME=/some/dir`.
A config file with another name moves to `config.yml` in its own folder, and
its `<name>.state.yml` to `state.yml` beside it; log in again there
(`untaped auth login <plugin>`), since stored logins are keyed by the
config file's path. `UNTAPED_STATE` has no replacement: state lives beside
the config. A plugin's tests write `home.path / "x"` where they wrote
`home.path / ".untaped" / "x"`.
