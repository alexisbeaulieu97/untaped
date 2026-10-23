# Credentials

Three rules decide where a plugin's settings and secrets go:

- a secret never lives in configuration: `untaped auth login` stores it in
  the machine's password store, or as a command that prints it;
- what decides where a secret is sent (the server's address, and how its
  certificate is checked) is stored with it, so a secret is only ever used
  for the exact server it was filed for;
- what applies to every request (the proxy, the timeout, root
  `http.ca_bundle_path`) stays outside, in core's `http` settings.

In code, the server is a `CredentialKey` subclass held by one marked setting,
and each secret is a `Credential` listed on `PluginSpec(credentials=...)`:

```python
from typing import Annotated

from pydantic import BaseModel, HttpUrl

from untaped.sdk import Credential, CredentialKey, NeedsSetup, TlsVerify, connected_client


class AcmeServer(CredentialKey):
    url: HttpUrl
    verify_tls: TlsVerify = True


class AcmeSettings(BaseModel):
    server: Annotated[AcmeServer | None, NeedsSetup] = None


TOKEN = Credential("acme", "token", key=AcmeServer, env=("ACME_TOKEN",))


def client(settings: AcmeSettings):
    server = settings.server  # None until set up: connected_client says so
    return connected_client(
        settings,
        section="acme",
        base_url=None if server is None else str(server.url),
        bearer=None if server is None else TOKEN.get(server),
        verify=True if server is None else server.verify_tls,
    )
```

`TOKEN.get(server)` answers from `UNTAPED_ACME__TOKEN`, then the login stored
for exactly this server, then (in 11.x) the old `acme.token` and
`acme.token_command` settings, then `ACME_TOKEN`; with none it raises
`NoCredential` (exit 4) naming `untaped auth login acme`, a step only the
person takes. The token is bound to the server's origin (scheme, host and
port): `connected_client` refuses to build a client for another one, and a
request elsewhere raises `HttpError` before it is sent. Redirects are
returned unless a call passes `follow_redirects=True`, which follows them on
the same origin only. In tests, `untaped.sdk.testing` keeps logins in memory
(`current_memory_store()`), so `TOKEN.store(server, token="t")` sets one up.

## See also

- [Building a plugin](./plugins.md) and
  [Configuration](./configuration.md#tokens): how users log in.
