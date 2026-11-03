# Server APIs

A plugin that talks to a server its owner upgrades on their own schedule
(an issue tracker, a code host, an automation controller) declares one
`ServerApi` block. Its commands call one **port**, a `Protocol` in the
plugin's own models; one **adapter** per API version serves it. Core finds
out which version the server runs (**detection**), picks the adapter, and
refuses a version none serves, so no command branches on a version.

The convention: a plugin supports every server version that exposes the
API it targets, latest first. The floor rises only when the plugin adopts a
break, and the old adapter then stays, deprecated, until the plugin's next
major, not until the vendor's end of support.

The block lives in `untaped.sdk.network`.

## Declaring one

```python
from dataclasses import dataclass
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, Field, HttpUrl

from untaped.sdk import Command, Output, Plugin, Refresh, key
from untaped.sdk.health import NeedsSetup
from untaped.sdk.network import (
    ApiAdapter,
    ApiMismatch,
    Credential,
    CredentialKey,
    HttpClient,
    Release,
    ServerApi,
    ServerVersion,
)


class AcmeServer(CredentialKey):
    url: HttpUrl


@dataclass(frozen=True)
class AcmeVersion(ServerVersion):
    """An Acme server: ``Acme 4.2``."""

    release: Release

    def __str__(self) -> str:
        return f"Acme {self.release}"

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls(Release.parse(text.removeprefix("Acme ")))


class AcmeSettings(BaseModel):
    server: Annotated[AcmeServer | None, NeedsSetup] = None
    server_version: AcmeVersion | None = None  # the override


class Ticket(BaseModel):
    key: str


class AcmeApi(Protocol):  # the port
    def ticket(self, key: str) -> Ticket: ...


def detect(settings: AcmeSettings, client: HttpClient) -> AcmeVersion:
    """Asks `GET /about`."""
    with ApiMismatch.watch():
        return AcmeVersion(Release.parse(client.get_json_dict("/about")["version"]))


class V2(ApiAdapter[AcmeSettings, AcmeVersion], AcmeApi):
    """Acme 4 and later.

    REST API 2.
    """

    recorded = (AcmeVersion.parse("Acme 4.2"),)

    @classmethod
    def serves(cls, version: AcmeVersion) -> bool:
        return version.release >= (4,)

    def ticket(self, key: str) -> Ticket:
        with ApiMismatch.watch():
            return Ticket.model_validate(self.connect().get_json_dict(f"/api/2/tickets/{key}"))


TOKEN = Credential(key=AcmeServer, env=("ACME_TOKEN",))

SERVER_API = ServerApi[AcmeApi, AcmeSettings, AcmeVersion](
    server=key(AcmeSettings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(V2,),
    override=key(AcmeSettings).server_version,
)


class Show(Command[AcmeSettings]):
    """Show a ticket."""

    key: str = Field(description="The ticket's key.")
    refresh: Refresh = Refresh(SERVER_API.cache)
    output: Output[Ticket]

    def run(self) -> Ticket:
        port = SERVER_API.port(self.settings, refresh=self.refresh.directive)
        return port.ticket(self.key)


class Acme(Plugin):
    """Work with Acme tickets."""

    settings = AcmeSettings
    commands = (Show,)
    token = TOKEN
    server_api = SERVER_API
```

- The **version type** subclasses `ServerVersion` as a frozen dataclass of
  the facts adapters choose by. `str()` is how people read and set it;
  `parse` reads it back. `Release` is a dotted release that compares as a
  tuple.
- **`detect(settings, client)`** asks the server which version it runs,
  through a client core builds for `server=`. Its docstring says what it
  asks; the plugin's page shows it.
- Each **adapter** subclasses `ApiAdapter` and the port. Its docstring's
  first line names the servers it serves, as people read them in messages
  and docs; the rest says how it talks. `recorded` lists the versions its
  tests ran against; `serves(version)` says whether it serves one. It
  reaches the server only through `self.connect()`.
- **`override`** is a setting typed `V | None`; set, it replaces detection.
- Adapters are tried in declared order, latest first; no two may serve one
  recorded version.

Composition refuses a block that can't work and names the class: an
unsubscripted block, keys outside the plugin's settings, an adapter without
a docstring or recorded versions, two adapters serving one recorded version,
a version whose `parse` doesn't read back its `str`, a deprecated adapter
without its major.

## What core does

On a port's first call, core checks the plugin is set up, then takes the
version from the override, else the detection cache (one entry per server
URL, trusted for `max_age`, one day by default, tunable with
`<plugin>.caches.server_api.max_age_seconds`), else detects now. It picks
the first adapter serving that version and builds it with the command's
settings. A `Refresh(SERVER_API.cache)` field gives the command
`--refresh` and `--cached`.

- **A version no adapter serves** exits 4 with the versions the plugin
  supports. **A server that doesn't answer as the product** exits 4 too,
  offering the override.
- **A mismatch**: inside `ApiMismatch.watch()`, an answer that doesn't
  read, or a 404 or 405 that isn't the vendor's own error
  (`vendor_error=`), becomes an `ApiMismatch`. Core detects once more; when
  the server changed, it switches adapter and runs the call again if no
  write went through and nothing was printed. Otherwise it exits 1 (a write
  may have taken effect) or 5 (nothing took effect; run it again).
- **A deprecated adapter** (`stability = deprecated(removed_in=13,
  replacement=V2)`) warns on runs that detected the version or read the
  override, naming the server and the release that removes it.

Each block also gets, with nothing more to declare:

- a doctor row `<plugin>.server-api`: offline it reads the cache or the
  override; `untaped doctor --online` detects again and warns when the
  override disagrees with the server;
- a "Servers" section on the plugin's page (`untaped docs PLUGIN`): the
  detection sentence, `detect`'s docstring and one row per adapter;
- surface lines `server-api <plugin>.server-api` and one `adapter` line per
  class, so dropping an adapter before its major fails the release check.

## Testing

Record each recorded version's answers under
`tests/<package>/recorded/<version>/` and run each command test once per
adapter and recorded version, pinning the version with the override
(`install(Acme, settings=AcmeSettings(server=..., server_version=v))`) and
serving the routes with `respx`. Test `detect` against each recorded
discovery answer, plus one URL that answers but not as the product.

## See also

- [Credentials](./credentials.md): the server key and token the block reads.
- [Caches](./caches.md): the detection cache and `--refresh`.
- [Exit codes](./reference/exit-codes.md).
