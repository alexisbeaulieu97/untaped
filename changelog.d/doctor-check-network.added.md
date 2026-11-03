A plugin's `DoctorCheck(network=...)` says when its check runs: `"offline"`
(the default), `"online"` (only where untaped may contact services:
`untaped doctor --online`, `untaped setup plan --online` and setup's check of
new values) or `"both"`, reading `PluginContext.online` to tell which.
