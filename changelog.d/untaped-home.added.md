`UNTAPED_HOME` moves the home, the one folder untaped keeps everything in
(`config.yml`, `state.yml`, `plugins/<name>/`); it defaults to `~/.untaped`
and must be an absolute path (`~` is expanded), else exit 4. Plugins read it
through `untaped.sdk.home()`, and a command reads its own folder as
`self.ctx.plugin.dir`.
A plugin may not be named `home`.
