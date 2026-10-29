**Breaking (plugins):** a block no longer takes its plugin and name by hand:
`Cache("acme", "motd", ...)`, `Credential("acme", "token", ...)` and
`DoctorCheck("acme.check", ...)` are gone, and so are `Cache(store=)`,
`Store`, `setting_guard`, `retired_values`, `refresh_option`,
`executable_check` and `online_check`. Composition no longer reports
`foreign-cache`, `duplicate-cache`, `foreign-store`, `duplicate-store`,
`foreign-credential` or `duplicate-credential`: a plugin class's attributes
name its blocks, so neither can happen.
