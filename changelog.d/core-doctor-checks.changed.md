Core's own doctor checks are `DoctorCheck` attributes on its plugin class,
so their rows are named like any plugin's: `untaped.config-permissions`
(was `config`), `untaped.unknown-keys` (was `unknown-keys`) and
`untaped.token-store` (was `config`), which now shows a passing row when no
`token_command` runs `pass`. They run with the other plugins' checks. The
`caches` row also reads core's own cache of the composed plugins.
