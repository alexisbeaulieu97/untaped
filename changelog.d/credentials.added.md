Tokens leave `config.yml`. `untaped auth login [<plugin> [<name>]]` files a
token for the server this run uses (its URL and how its certificate is
checked) in the macOS keychain, a Secret Service or `pass`, or with
`--token-command` as a command that prints it; a token is then only ever sent
to that server. `auth list` shows every stored login, `auth forget` deletes
one (`--id` reaches any) and `auth status` says which source answers each
token for this run. `UNTAPED_<PLUGIN>__TOKEN` still overrides for one
process, and a missing token exits 4 naming `auth login`, marked as the
person's step (`fix_by: user`) so an agent hands it over
([tokens](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/configuration.md#tokens)).
A plugin declares its secrets as `Credential`s on `PluginSpec(credentials=)`,
for a server held by a `CredentialKey` setting, and reads one with
`Credential.get`; `untaped.testing` keeps logins in memory in tests
([plugins.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/plugins.md#credentials)).
