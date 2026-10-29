`Credential.login` is the `untaped auth login` command that files a
credential (`auth login awx`, `auth login git hosts`), and
`Credential.login_args` what `auth login` and `auth forget` take to name it,
so a plugin's hint spells the command the way core does.
