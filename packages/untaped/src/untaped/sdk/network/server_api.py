"""Server APIs: a plugin's commands talk to one port, served by one adapter per API version.

A plugin that talks to a server its owner upgrades on their own schedule
declares one :class:`ServerApi` on its class: the setting holding the
server's :class:`~untaped.sdk.network.CredentialKey` and its credential, its
**port** (a ``Protocol`` in the plugin's own models), how to **detect** the
server, its **version type** (a :class:`ServerVersion`), its **adapters**
(one :class:`ApiAdapter` per API version, each saying which versions it
serves) and the setting that **overrides** detection. Commands call
``SERVER_API.port(self.settings)`` and see only the port.

Core does the rest: on the port's first call it checks the plugin is set
up, takes the version from the override, else the detection cache, else
detects now, and picks the first adapter in declared order whose
``serves`` accepts it. A version no adapter serves is refused with exit 4.
After an :class:`ApiMismatch` from a port call, core re-detects once; when
the server changed to a version another adapter serves, it switches, and
runs the call again on it when nothing can have happened yet (no write went
through, nothing printed), else exits 1 ("may have taken effect") or 5
("run it again"). A deprecated adapter warns on runs that detect or read
the override. The port's proxy, its pick and the rerun live in
:mod:`untaped.sdk.network._server_api_port`. The block's parts are its
detection cache and its doctor row (:mod:`untaped.sdk.network._server_api_doctor`);
the plugin's page and the surface read its adapters from the declaration.

Core knows the server only from ``server=`` and the product only from
``str(version)``; it compares no versions (adapters compare their own
:class:`Release` fields) and decides no mismatch (an adapter raises it).
"""

from __future__ import annotations

import inspect
import threading
import typing
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast, get_args

from pydantic import BaseModel, ValidationError

from untaped.sdk._block import Block, OwnedBlock
from untaped.sdk._keys import SettingKey
from untaped.sdk.errors import (
    ConfigError,
    ErrorCategory,
    HttpError,
    HttpStatusError,
    UntapedError,
    first_validation_error,
)
from untaped.sdk.health.doctor import DoctorCheck
from untaped.sdk.marks import experimental
from untaped.sdk.network._server_api_calls import last_sent
from untaped.sdk.network._versions import Release, ServerVersion
from untaped.sdk.network.http import sent_by
from untaped.sdk.storage.cache import Cache

if TYPE_CHECKING:
    from untaped.sdk.health.doctor import DoctorResult, PluginContext
    from untaped.sdk.marks import Stability
    from untaped.sdk.network.credentials import Credential
    from untaped.sdk.network.http import HttpClient


# --------------------------------------------------------------------------- #
# Mismatches
# --------------------------------------------------------------------------- #


def _no_vendor_error(error: HttpStatusError) -> bool:
    """Recognise no 404 as the vendor's own: detection's default."""
    del error
    return False


@experimental
class ApiMismatch(UntapedError):
    """The server answered as this API version never does: a gone path, a body that doesn't read.

    An adapter raises it, usually through :meth:`watch`; nothing else counts
    as a mismatch (a ``ValidationError`` outside ``watch``, the vendor's own
    not-found, a 401 pass through as they are). ``request`` names what the
    server was asked (``POST /rest/api/2/search``) and ``answer`` how it
    answered, when known. Whether anything took effect is core's to tell
    from the requests it saw, never a field the plugin sets. Passed through,
    it exits 1.

    Experimental. First consumer: jira.
    """

    category = ErrorCategory.FAILED
    #: What the server was asked: ``POST /rest/api/2/search``.
    request: str | None
    #: How it answered: ``HTTP 404``.
    answer: str | None

    def __init__(
        self,
        message: str,
        *,
        request: str | None = None,
        answer: str | None = None,
        **attributed: Any,
    ) -> None:
        super().__init__(message, **attributed)
        self.request = request
        self.answer = answer
        #: Core passed it through after its own handling: a port call around it doesn't again.
        self._passed = False

    @classmethod
    @contextmanager
    def watch(
        cls, *, vendor_error: Callable[[HttpStatusError], bool] = _no_vendor_error
    ) -> Iterator[None]:
        """Turn what the server answered unexpectedly inside the block into a mismatch.

        A pydantic ``ValidationError`` or an unreadable 2xx body becomes one,
        and so does a 404 or 405 that ``vendor_error`` does not recognise as
        the vendor's own error (Jira's ``errorMessages``): that response
        accepted nothing. A recognised one, and every other error, pass
        through. Scope it to talking and parsing, never to the user's input.
        The default recognises no 404 as the vendor's, which fits detection.
        """
        try:
            yield
        except ValidationError as exc:
            sent = last_sent()
            answer = f"an answer that does not read ({first_validation_error(exc)})"
            raise cls._made(None if sent is None else str(sent), answer) from exc
        except HttpStatusError as exc:
            if exc.status_code not in (404, 405) or vendor_error(exc):
                raise
            sent = sent_by(exc)
            if sent is not None:
                sent.accepted_nothing = True
            asked = str(sent) if sent is not None else exc.url
            raise cls._made(asked, f"HTTP {exc.status_code}") from exc
        except HttpError as exc:
            if exc.status_code is None or exc.status_code >= 400:
                raise
            sent = last_sent()
            asked = str(sent) if sent is not None else exc.url
            raise cls._made(
                asked, f"HTTP {exc.status_code} with a body that does not read"
            ) from exc

    @classmethod
    def _made(cls, request: str | None, answer: str) -> ApiMismatch:
        asked = "the server" if request is None else request
        return cls(f"{asked} answered {answer}", request=request, answer=answer)


class UnsupportedServer(ConfigError):
    """The server runs a version none of the plugin's adapters serves (exit 4); core raises it."""


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #


@experimental
class ApiAdapter[S: BaseModel, V: ServerVersion]:
    """Base of one API version's adapter: it owns that version's paths, JSON, paging and auth.

    A subclass also subclasses the plugin's port, and declares:

    - its docstring, whose first line names the servers it serves (people
      read it in messages and docs) and whose rest says how it talks;
    - ``recorded``, the versions its tests ran against (recorded responses),
      each of which it must serve;
    - ``serves(version)``, a classmethod saying whether it serves a version;
    - optionally ``stability = deprecated(removed_in=13, replacement=Newer)``.

    Core builds it with the plugin's ``settings`` and the ``version`` it
    picked. :meth:`connect` is the one way it reaches the server.

    Experimental. First consumer: jira.
    """

    stability: ClassVar[Stability | None] = None
    #: The plugin's settings, as the command calling the port has them.
    settings: S
    #: The version core picked this adapter for.
    version: V

    def __init__(self, settings: S, version: V) -> None:
        self.settings = settings
        self.version = version
        self._connector: Callable[[Mapping[str, str] | None], HttpClient] | None = None
        self._clients: dict[tuple[tuple[str, str], ...], HttpClient] = {}
        self._clients_lock = threading.Lock()

    def connect(self, *, headers: Mapping[str, str] | None = None) -> HttpClient:
        """The client for the server, sending ``headers`` on every request.

        Exactly what ``ctx.http.client`` builds for the ``server=`` setting's
        URL, TLS and credential, so origin binding, the proxy rules and TLS
        live in one place. One client per ``headers`` for the adapter's life,
        so a search's pages and ``Parallel.map`` threads share connections.
        Core records each request it sends, to tell after a mismatch whether
        a write went through.
        """
        if self._connector is None:
            raise RuntimeError(
                f"{type(self).__qualname__} was not built by its ServerApi: commands reach "
                "an adapter through SERVER_API.port(settings)"
            )
        memo = tuple(sorted((headers or {}).items()))
        with self._clients_lock:
            client = self._clients.get(memo)
            if client is None:
                client = self._clients[memo] = self._connector(headers)
        return client


class AdapterClass[S: BaseModel, V: ServerVersion, P](Protocol):
    """What ``ServerApi(adapters=...)`` takes: an adapter class serving port ``P``.

    Typed so that an adapter missing a port method, with no port, recording
    another plugin's version or taking other settings fails the type check
    at the ``ServerApi(...)`` line.
    """

    @property
    def recorded(self) -> tuple[V, ...]: ...

    def __call__(self, settings: S, version: V) -> P: ...

    def serves(self, version: V) -> bool: ...


def summary_line(adapter: type) -> str:
    """The first line of ``adapter``'s own docstring: the servers it serves, as people read it."""
    doc = inspect.cleandoc(adapter.__dict__.get("__doc__") or "")
    return doc.splitlines()[0].strip().rstrip(".") if doc else adapter.__name__


# --------------------------------------------------------------------------- #
# The block
# --------------------------------------------------------------------------- #


class _Detected(BaseModel):
    """What the detection cache keeps per server URL: the version, as ``str(version)``."""

    version: str


@experimental
class ServerApi[P, S: BaseModel, V: ServerVersion](OwnedBlock):
    """A plugin's server API: its port, detection, adapters and override.

    Declared subscripted, ``ServerApi[JiraApi, JiraSettings, JiraVersion](...)``,
    and held by the plugin class (``server_api = SERVER_API``), whose
    attribute names it: the detection cache is ``<plugin>.server_api``
    (``Refresh(SERVER_API.cache)`` names it), the doctor row, messages and
    warnings say ``<plugin>.server-api``.

    ``server`` is ``key(S)`` of the field holding the plugin's
    ``CredentialKey`` (its URL and TLS), ``credential`` the plugin's
    credential for that key, sent when one is set. ``detect(settings,
    client)`` asks the server which version it runs, through a client core
    builds; ``adapters`` are tried in declared order; ``override`` is
    ``key(S)`` of a field typed ``V | None`` that, set, replaces detection.
    ``max_age`` is how long a detection is trusted.

    Experimental. First consumer: jira.
    """

    block_kind = "server-api"
    name_style = "kebab"

    server: SettingKey
    credential: Credential[Any]
    detect: Callable[[S, HttpClient], V]
    adapters: tuple[AdapterClass[S, V, P], ...]
    override: SettingKey
    #: The detection cache: one entry per server URL, ``str(version)``.
    cache: Cache[_Detected]
    #: The doctor row ``<plugin>.server-api``: which server, served by what.
    doctor: DoctorCheck

    def __init__(
        self,
        *,
        server: SettingKey,
        credential: Credential[Any],
        detect: Callable[[S, HttpClient], V],
        adapters: Sequence[AdapterClass[S, V, P]],
        override: SettingKey,
        max_age: timedelta = timedelta(days=1),
    ) -> None:
        from untaped.sdk.network.credentials import Credential  # noqa: PLC0415 - no cycle

        super().__init__()
        if not isinstance(server, SettingKey) or not isinstance(override, SettingKey):
            raise TypeError("ServerApi(server=, override=) take key(Settings).field")
        if not isinstance(credential, Credential):
            raise TypeError(f"ServerApi(credential=) takes a Credential, not {credential!r}")
        if not callable(detect):
            raise TypeError(f"ServerApi(detect=) takes a function, not {detect!r}")
        self.server = server
        self.credential = credential
        self.detect = detect
        self.adapters = tuple(adapters)
        self.override = override
        self.cache = Cache(label="server version", max_age=max_age, model=_Detected, split="none")
        self.doctor = DoctorCheck(title="server API", run=self._doctor_row, network="both")
        self._picks: dict[tuple[int, str, str | None], Any] = {}
        self._picks_lock = threading.Lock()

    def _parts(self) -> Mapping[str, Block]:
        """Its detection cache ``<plugin>.server_api`` and doctor row ``<plugin>.server-api``."""
        return {"cache": self.cache, "doctor": self.doctor}

    def _doctor_row(self, ctx: PluginContext) -> DoctorResult:
        from untaped.sdk.network._server_api_doctor import server_api_row  # noqa: PLC0415

        return server_api_row(self, ctx)

    def __repr__(self) -> str:
        return f"ServerApi({self.where!r})"

    @property
    def qualified(self) -> str:
        """``<plugin>.<name>``: ``jira.server-api``."""
        return f"{self.plugin}.{self.name}"

    def port(self, settings: S, *, refresh: bool | None = None) -> P:
        """The port, served by the adapter core picks for ``settings``' server.

        Nothing is sent until the first call: then core checks the plugin is
        set up, finds the version and picks. ``refresh`` is a ``Refresh``
        field's directive naming :attr:`cache` (``True`` detects again now,
        ``False`` reads only the cache); the override, when set, wins. Ports
        built from one settings object in one run share their adapters and
        connections, so pass the command's own ``self.settings``.
        """
        from untaped.sdk.network._server_api_port import Port  # noqa: PLC0415 - no cycle

        return cast("P", Port(self, settings, refresh))

    # ---- what core reads ----------------------------------------------------

    def _arguments(self) -> tuple[Any, Any, Any] | None:
        """``(P, S, V)`` as subscripted; ``None`` when the block was not subscripted."""
        args = get_args(getattr(self, "__orig_class__", None))
        return (args[0], args[1], args[2]) if len(args) == 3 else None

    @property
    def _version_type(self) -> type[V]:
        """``V``, the version type the block was subscripted with."""
        arguments = self._arguments()
        if arguments is None:
            raise TypeError(f"{self.where} names no version type: subscript it")
        return cast("type[V]", arguments[2])

    @property
    def _url_field(self) -> str | None:
        """The field of the ``server=`` key holding the URL people set (``url``), if one does."""
        from untaped.sdk.network.credentials import CredentialKey, key_shape  # noqa: PLC0415

        shape = key_shape(cast("type[CredentialKey]", self.credential.key))
        return shape.token_url or (shape.urls[0] if shape.urls else None)

    @property
    def _members(self) -> frozenset[str]:
        """The port's members: all a command reaches through :meth:`port`."""
        arguments = self._arguments()
        if arguments is None or not getattr(arguments[0], "_is_protocol", False):
            raise TypeError(f"{self.where} names no port Protocol: subscript it")
        return frozenset(typing.get_protocol_members(arguments[0]))

    # ---- composition --------------------------------------------------------

    def problems(self) -> tuple[str, ...]:
        from untaped.sdk.network._server_api_checks import problems  # noqa: PLC0415 - composition

        return problems(self, self._arguments(), self._owner_settings())

    def _owner_settings(self) -> object:
        """The settings model of the plugin class holding the block, if any holds it."""
        return getattr(self.bindings[0].owner, "settings", None) if self.bindings else None


__all__ = [
    "AdapterClass",
    "ApiAdapter",
    "ApiMismatch",
    "Release",
    "ServerApi",
    "ServerVersion",
    "UnsupportedServer",
    "summary_line",
]
