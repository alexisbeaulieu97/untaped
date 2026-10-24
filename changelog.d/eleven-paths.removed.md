**Breaking (users):** `untaped.http.verify_ssl` and
`untaped.http.verify_hostname`: turn certificate checks off for one server
with its `verify_tls` (`untaped config set awx.server.verify_tls false`), or
trust a CA file there. `config migrate` no longer moves tokens or TLS
settings, and drops its `--yes`.
