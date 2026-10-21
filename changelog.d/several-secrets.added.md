A plugin can mark several `Secret` settings, and `untaped auth` stores each:
`auth set|unset <section>` means the one named `token` (else the only one),
and `auth set <section>.<field>` names any of them, in its own store entry
`<profile>/<section>.<field>`. `auth status` has a row per secret, `auth
migrate` moves each, and the `untaped.auth_outcome` and `untaped.token_source`
records gain `field`. A plugin builds its client from values:
`connected_client(config, section=..., base_url=config.base_url)` sends the
`token` (or only) secret as the bearer, `bearer=` overrides it, and
`require_token=False` builds one for an unauthenticated call
([plugins.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/plugins.md#settings-and-the-plugin-app)).
