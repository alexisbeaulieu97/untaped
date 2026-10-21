A plugin whose settings have `base_url` and `token` fields (or a
`token_sources` attribute) but no `NeedsSetup` or `Secret` marks, and a
plugin declaring the `<field>_command` setting untaped adds for a `Secret`,
are deprecated and removed in 12.0. Both still work: the first warns once
per run where `setup`, `setup plan`, `doctor` or `auth` reads it, and
`untaped lint` reports each as a warning (`lint.unmarked-service`,
`lint.self-declared-command`).
