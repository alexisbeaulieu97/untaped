Declare the plugin as a `Plugin` class instead of a `PluginSpec`, its skill as
`skill = StubSkill("...")` and a deprecated behaviour as `row_kind =
DeprecatedBehaviour(removed_in=..., replacement=...)` on it. A command reads
its folder as `self.ctx.plugin.dir` (`Project.plugin_dir(spec)` has no
successor: build `project.root / ".untaped" / "plugins" / "<plugin>"`), its
settings as `self.settings` (other code in the run as `settings_of(Model)`
for `app_context().section(...)`), its stderr and HTTP clients as
`self.ctx.diag` and `self.ctx.http`, and its files under `.untaped/` as
`self.ctx.plugin.committed(name, root=...)`. A renamed command keeps its old
name with `aliases = (Deprecated(replacement="old-name", removed_in=...),)`
on its class; a renamed flag or group has no alias, so rename one only in a
major. A deprecated flag carries `deprecated(...)` on its field, and mount
puts it under Deprecated options. Delete any
`# untaped: allow foreign-section` marker.
