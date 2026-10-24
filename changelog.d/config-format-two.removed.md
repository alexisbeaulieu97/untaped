**Breaking (users):** `untaped profile use` and the `active:` key are gone:
nothing in `config.yml` picks a profile. A run reads the profile `--profile`
or `UNTAPED_PROFILE` names, else only the base; `profile current` reports
`flag`, `env` or `fallback`.
