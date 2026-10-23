`untaped.sdk` has the run side of the 12.0 plugin interface, not yet
mounted: `settings_of(Model)` and `plugin_of(Model)` name a plugin by its
settings model (its settings, its folder, its committed files); a command
reads `self.settings` and `self.ctx` (`diag`, `terminal`, `project`,
`plugin`, `plugins`); and an error's `system` is a `System` (`UNTAPED`,
`LOCAL`, `GIT`, `HTTP`) or `Service(Model)` for a plugin's own service.
