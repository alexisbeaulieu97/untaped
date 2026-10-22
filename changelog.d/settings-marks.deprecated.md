A plugin whose settings have `base_url` and `token` fields (or a
`token_sources` attribute) but no `NeedsSetup` mark is deprecated and removed
in 12.0. It still works, warns once per run where `setup`, `setup plan`,
`doctor` or `auth` reads it, and `untaped lint` reports it as a warning
(`lint.unmarked-service`).
