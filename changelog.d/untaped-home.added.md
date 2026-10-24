`UNTAPED_HOME` moves the home, the one folder untaped keeps everything in
(`config.yml`, `state.yml`, `plugins/<name>/`); it defaults to `~/.untaped`
and must be an absolute path (`~` is expanded), else exit 4. Plugins read it
through `untaped.sdk.home()` and `untaped.sdk.plugin_path(name, *parts)`.
A plugin may not be named `home`.
