A token in `<plugin>.token` or `<plugin>.token_command`, `auth set`, `auth
unset` and `auth migrate`, the SDK's `TokenCommand` and `TokenSources`,
`GithubSettings.base_url` and `GithubSettings.token`, root
`http.verify_ssl` (read as each server's `verify_tls: false` until then) and
`http.verify_hostname`, and a git credential helper line without `--nonce`
are deprecated and removed in 12.0. `UNTAPED_<PLUGIN>__TOKEN` stays.
