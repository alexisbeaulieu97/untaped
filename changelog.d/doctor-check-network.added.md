A plugin's `DoctorCheck(network=...)` says when its check runs: `"offline"`
(the default), `"online"` (only under `untaped doctor --online` and in
`untaped setup`'s plan) or `"both"`, reading `PluginContext.online` to tell
which.
