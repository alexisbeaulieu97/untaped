An `awx`, `github` or `jira` command run before the service is set up now
fails with `<section> is not set up`, naming the marked settings still unset,
with `untaped setup plan --only <section>` as the hint. A plain-text secret
other than `token` in `config.yml` warns too, naming `untaped auth migrate`,
and the fix `setup plan` and `doctor` give for a secret other than `token` is
`untaped auth set <section>.<field>` (bare `<section>` when it is the only
one).
