**Breaking (users):** `UNTAPED_CONFIG` and `UNTAPED_STATE` are gone:
`config.yml` and `state.yml` are always at the home's root, so a different
config file means a different home (`UNTAPED_HOME`). A command run with
either still set exits 4 naming `UNTAPED_HOME`, rather than writing to
`~/.untaped`.
