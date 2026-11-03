"""A toy plugin with a server API block, for the block's tests.

``Toy`` holds the block; its commands call the port. Its version type is
``Toy <release>``; ``V2`` serves Toy 3 and later under ``/v2/``, ``V1``
serves Toy 1 to 2 under ``/v1/``. Detection asks ``GET /about``. Requests
are served by ``respx`` (:func:`serve`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, Protocol, Self

import httpx
import respx
from pydantic import BaseModel, Field, HttpUrl

from untaped.sdk import Command, Output, Plugin, Refresh, key
from untaped.sdk.errors import HttpStatusError
from untaped.sdk.health import NeedsSetup
from untaped.sdk.network import (
    ApiAdapter,
    ApiMismatch,
    Credential,
    CredentialKey,
    HttpClient,
    Release,
    RetryPolicy,
    ServerApi,
    ServerVersion,
    TlsVerify,
)

URL = "https://toy.example.com"
#: A search is a POST its policy calls idempotent: a read.
SEARCH = RetryPolicy(idempotent_methods=frozenset({"GET", "POST"}))


class ToyServer(CredentialKey):
    """The toy server a token is for."""

    url: HttpUrl = Field(description="The toy server's URL.")
    verify_tls: TlsVerify = Field(default=True, description="Check its certificate.")


@dataclass(frozen=True)
class ToyVersion(ServerVersion):
    """A toy server: ``Toy 1.4``."""

    release: Release

    def __str__(self) -> str:
        return f"Toy {self.release}"

    @classmethod
    def parse(cls, text: str) -> Self:
        if not text.startswith("Toy "):
            raise ValueError(f"a toy version reads 'Toy 1.4', not {text!r}")
        return cls(Release.parse(text.removeprefix("Toy ")))


class Item(BaseModel):
    """One toy item."""

    name: str


class About(BaseModel):
    version: Release


class ToyApi(Protocol):
    def item(self, name: str) -> Item: ...

    def items(self) -> Iterator[Item]: ...

    def add(self, name: str) -> Item: ...

    def search(self, text: str) -> list[Item]: ...


def is_toy_error(error: HttpStatusError) -> bool:
    """The toy server's own 404 carries ``toyError``."""
    return "toyError" in (error.body or "")


def detect(settings: BaseModel, client: HttpClient) -> ToyVersion:
    """Asks `GET /about`."""
    with ApiMismatch.watch():
        about = About.model_validate(client.get_json_dict("/about"))
    return ToyVersion(about.version)


class _Talks(ApiAdapter[Any, ToyVersion]):
    """Where both adapters' calls live: only the prefix differs."""

    prefix = ""

    def item(self, name: str) -> Item:
        with ApiMismatch.watch(vendor_error=is_toy_error):
            return Item.model_validate(self.connect().get_json_dict(f"{self.prefix}/items/{name}"))

    def items(self) -> Iterator[Item]:
        client = self.connect()
        for page in (1, 2):
            with ApiMismatch.watch(vendor_error=is_toy_error):
                raws = client.get_json_list(f"{self.prefix}/items", params={"page": page})
                rows = [Item.model_validate(raw) for raw in raws]
            yield from rows

    def add(self, name: str) -> Item:
        with ApiMismatch.watch(vendor_error=is_toy_error):
            raw = self.connect().post_json(f"{self.prefix}/items", json={"name": name})
            return Item.model_validate(raw)

    def search(self, text: str) -> list[Item]:
        with ApiMismatch.watch(vendor_error=is_toy_error):
            raws = self.connect().request_json(
                "POST", f"{self.prefix}/search", json={"text": text}, retry=SEARCH
            )
            return [Item.model_validate(raw) for raw in raws]


class V2(_Talks, ToyApi):
    """Toy 3 and later.

    API v2.
    """

    prefix = "/v2"
    recorded = (ToyVersion.parse("Toy 3.0"),)

    @classmethod
    def serves(cls, version: ToyVersion) -> bool:
        return version.release >= (3,)


class V1(_Talks, ToyApi):
    """Toy 1 to 2.

    API v1.
    """

    prefix = "/v1"
    recorded = (ToyVersion.parse("Toy 1.4"),)

    @classmethod
    def serves(cls, version: ToyVersion) -> bool:
        return (1,) <= version.release < (3,)


class ToySettings(BaseModel):
    """The toy plugin's settings."""

    server: Annotated[ToyServer | None, NeedsSetup] = Field(
        default=None, description="The toy server."
    )
    server_version: ToyVersion | None = Field(
        default=None, description="The toy server's version, instead of detecting it."
    )


TOKEN = Credential(key=ToyServer, env=("TOY_TOKEN",))

SERVER_API = ServerApi[ToyApi, ToySettings, ToyVersion](
    server=key(ToySettings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(V2, V1),
    override=key(ToySettings).server_version,
)


def configured(*, version: str | None = None, url: str = URL) -> ToySettings:
    """Settings naming the server at ``url``, and the override when ``version`` is given."""
    return ToySettings.model_validate({"server": {"url": url}, "server_version": version})


class Get(Command[ToySettings]):
    """Show one item."""

    name: str = Field(description="The item.")
    refresh: Refresh = Refresh(SERVER_API.cache)
    output: Output[Item]

    def run(self) -> Item:
        return SERVER_API.port(self.settings, refresh=self.refresh.directive).item(self.name)


class Items(Command[ToySettings]):
    """List items as they come."""

    output: Output[Item] = Output(stream=True)

    def run(self) -> Iterator[Item]:
        yield from SERVER_API.port(self.settings).items()


class Collect(Command[ToySettings]):
    """List items once all are read."""

    output: Output[Item]

    def run(self) -> Iterator[Item]:
        items = list(SERVER_API.port(self.settings).items())
        yield from items


class Add(Command[ToySettings]):
    """Add an item."""

    name: str = Field(description="The item.")
    output: Output[Item]

    def run(self) -> Item:
        return SERVER_API.port(self.settings).add(self.name)


class Search(Command[ToySettings]):
    """Search items."""

    text: str = Field(description="What to search for.")
    output: Output[Item]

    def run(self) -> Iterator[Item]:
        yield from SERVER_API.port(self.settings).search(self.text)


class Toy(Plugin):
    """The toy plugin (a test's)."""

    settings = ToySettings
    commands = (Get, Items, Collect, Add, Search)
    token = TOKEN
    server_api = SERVER_API


def serve(version: str | None, *, status: int = 200) -> respx.Route:
    """Answer ``GET /about`` with ``version`` (``None``: a 404 page: a server that isn't a toy)."""
    route = respx.get(f"{URL}/about")
    if version is None:
        return route.mock(return_value=httpx.Response(404, text="<html>not here</html>"))
    return route.mock(return_value=httpx.Response(status, json={"version": version}))


def route(method: str, path: str, *, status: int = 200, json: object = None) -> respx.Route:
    """Answer ``method path`` on the toy server with ``json`` (a 404 page for status 404)."""
    answer = (
        httpx.Response(status, text="<html>gone</html>")
        if status == 404 and json is None
        else httpx.Response(status, json=json)
    )
    return respx.route(method=method, url__startswith=f"{URL}{path}").mock(return_value=answer)
