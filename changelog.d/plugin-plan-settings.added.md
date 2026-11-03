A plugin writes its own settings through
`self.ctx.plugin.plan_settings({key(Settings).name: value})` (experimental):
the changes are checked together before anything is written, `preview()`
feeds `Confirm(preview=)`, and `write()` writes where `config set` writes, a
`HomeOnly` setting to the base.
