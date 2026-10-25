An install's reference now lists the plugins it composes (`plugins`), written
by `setup register` and kept current by the install's runs, so `setup prune`
keeps the folder of a plugin another install sharing the home uses. A
reference without the list names no plugins. `write_reference` in
`untaped.sdk.testing` takes `plugins=`.
