The plain-text `<section>.token` warning reads the same wherever it appears
(loading, `config set`, `doctor` and `setup plan`): "support for a plain-text
awx.token in config.yml is deprecated and will be removed in 12.0". Under
`--format json` it is an `untaped.deprecation` line with the id
`behaviour untaped.plaintext-token`, and `config set <section>.token` names
`untaped auth set <section>` as its replacement instead of a hint.
