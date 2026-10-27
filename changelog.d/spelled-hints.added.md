A hint names a command by its class: `run_hint(Set.spelled(key(...), "URL"))`,
`hint(...)`, `command_line(...)` (now exported) and an error's
`hint=Login.spelled("github")` spell it as the run mounts it, so a renamed
command or key follows its class. `untaped.core.api` exports the core
commands a plugin names (`Set`, `Login`, `Rank`), and lint's
`import-boundary` rule admits it. `http_of(Settings)` is a plugin's
`self.ctx.http` for its own code another plugin's command calls, as
`settings_of` is for its settings.
