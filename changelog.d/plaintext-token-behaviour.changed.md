A plugin that still reads a plain-text `<section>.token` (one that declares
no `Credential`) warns the same wherever it appears (loading, `config set`,
`doctor` and `setup plan`): "support for a plain-text acme.token in
config.yml is deprecated and will be removed in 12.0". Under `--format json`
it is an `untaped.deprecation` line with the id
`behaviour untaped.plaintext-token`, and `config set acme.token` names
`untaped auth set acme` as its replacement instead of a hint.
