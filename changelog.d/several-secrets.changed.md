An `awx`, `github` or `jira` command run before the service is set up now
fails with `<section> is not set up`, naming the marked settings still unset,
with `untaped setup plan --only <section>` as the hint. A plain-text secret
other than `token` in `config.yml` warns too, naming `untaped auth migrate`.
`setup plan` shows a set `SecretStr` value as `set`, and a `doctor --online`
failure says it `is set`, rather than printing `***`.
