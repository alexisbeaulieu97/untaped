"""The port's proxy: picking an adapter, refusing a server, and switching after a mismatch.

``ServerApi.port(settings)`` returns a :class:`Port`. Reading one of the
port's members returns a function and sends nothing; calling it checks the
plugin is set up, finds the version and picks the adapter (:class:`Pick`),
then forwards the call. Only the port's members are reachable.

The pick is a memo on the block, keyed by the run's composition (one per
invocation, which its child runs and threads share), the server URL and the
override's text, under a lock: ``Parallel.map`` threads share one pick, and
a child run on another server or override gets its own. A pick holds the
adapter class and the version, and the adapters built from them, one per
caller's settings object and run, never shared across either: ports built
from one command's settings share an adapter and its connections, while a
child run or another profile gets its own, and an adapter's client is built
by the run connecting. Nothing holds a run, so a finished run's picks, and
the clients its adapters opened, are dropped.
Detection is the block's cache, keyed by the URL alone.

After an :class:`~untaped.sdk.network.server_api.ApiMismatch` from a port
call, core re-detects once per pick (never under the override): the same
version passes the mismatch through (exit 1); another served version
switches the pick, and the call runs again on the new adapter when no write
went through during it and nothing was printed or handed to the command;
otherwise it fails with exit 1 (a write went through, in the call or earlier
in the invocation) or 5 (nothing took effect, but output was printed or
items handed). A version nothing serves is the exit-4 refusal.
"""

from __future__ import annotations

import functools
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from untaped.sdk._keys import value_in
from untaped.sdk._run import current_run, setting_name
from untaped.sdk.errors import (
    ConfigError,
    ErrorCategory,
    HttpStatusError,
    Service,
    UntapedError,
    attribution,
)
from untaped.sdk.marks import Deprecated, notify_deprecation, owner, removal_text
from untaped.sdk.network._server_api_calls import (
    CallLog,
    logging_into,
    record,
    recording_only,
)
from untaped.sdk.network.credentials import key_origin, key_shape
from untaped.sdk.network.server_api import (
    ApiMismatch,
    ServerApi,
    ServerVersion,
    UnsupportedServer,
    _Detected,
    summary_line,
)
from untaped.sdk.storage.cache import Filled

if TYPE_CHECKING:
    from untaped.sdk._run import Run
    from untaped.sdk.network.credentials import CredentialKey
    from untaped.sdk.network.http import HttpClient
    from untaped.sdk.progress import ProgressHandle

type Source = Literal["detected", "cache", "setting"]


@dataclass
class Pick:
    """One server's pick in this invocation: the adapter class serving it, and what decided it."""

    lock: threading.RLock = field(default_factory=threading.RLock)
    adapter: Any = None
    version: ServerVersion | None = None
    source: Source = "detected"
    #: Whether a mismatch already re-detected this server: a later one passes through.
    redetected: bool = False
    #: Whether a rerun said the server changed: one warning per switch, whichever thread reruns.
    announced: bool = False

    #: The adapters built, by (class, version, caller's settings, run): the settings held by
    #: the adapter, the run by a weak reference, so a finished run's entry goes with it.
    built: dict[tuple[Any, ...], tuple[weakref.ref[object], Any]] = field(default_factory=dict)

    @property
    def chosen(self) -> tuple[Any, ServerVersion | None]:
        """The adapter class and version a call runs on (read under :attr:`lock`)."""
        return self.adapter, self.version

    def adapter_for(
        self,
        api: ServerApi[Any, Any, Any],
        chosen: tuple[Any, ServerVersion | None],
        settings: BaseModel,
    ) -> Any:
        """The adapter ``chosen`` names for ``settings`` in this run, built on first use."""
        adapter, version = chosen
        assert version is not None  # a chosen adapter has its version
        run = current_run()
        memo = (adapter, version, id(settings), id(run))
        with self.lock:
            held = self.built.get(memo)
            if held is not None and held[0]() is run:
                return held[1]
            built = adapter(settings, version)
            built._connector = functools.partial(connect, api, settings)
            weakref.finalize(built, _close, built._clients)
            self.built[memo] = (weakref.ref(run), built)
        # a finished child run's adapter goes with it, closing its clients
        weakref.finalize(run, self.built.pop, memo, None)
        return built


_INVOCATIONS: dict[int, CallLog] = {}
_INVOCATIONS_LOCK = threading.Lock()


def invocation_log(composition: object) -> CallLog:
    """What every port call of this invocation sent: whether anything took effect before."""
    with _INVOCATIONS_LOCK:
        log = _INVOCATIONS.get(id(composition))
        if log is None:
            log = _INVOCATIONS[id(composition)] = CallLog()
            weakref.finalize(composition, _INVOCATIONS.pop, id(composition), None)
    return log


def connect(
    api: ServerApi[Any, Any, Any], settings: BaseModel, headers: Mapping[str, str] | None
) -> HttpClient:
    """A client for the server ``settings`` name, as ``ctx.http.client`` builds it, recording.

    Built by the run connecting now, so a child run's credential and
    trust are its own.
    """
    key: CredentialKey | None = value_in(api.server, settings)
    url = None if key is None else key_origin(key)
    if key is None or not url:
        from untaped.sdk.network.http import _not_set_up  # noqa: PLC0415 - httpx's cost

        raise _not_set_up(settings, section=api.plugin)
    # Core reads the key's TLS field by its type, as ``ctx.http.client`` callers name it.
    shape = key_shape(type(key))
    verify = True if shape.tls is None else getattr(key, shape.tls)
    client = current_run().http.client(
        settings,
        base_url=url.rstrip("/"),
        bearer=api.credential.find(key),
        verify=verify,
        headers=headers,
    )
    client._record = record
    return client


def _close(clients: dict[Any, HttpClient]) -> None:
    for client in clients.values():
        client.close()


class Port:
    """What ``ServerApi.port(settings)`` returns: the port's members, forwarded to the pick."""

    def __init__(self, api: ServerApi[Any, Any, Any], settings: BaseModel, refresh: bool | None):
        self._api = api
        self._settings = settings
        self._refresh = refresh
        self._refresh_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"<port of {self._api.where}>"

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_") or name not in self._api._members:
            raise AttributeError(f"{name!r} is not a member of {self._api.qualified}'s port")
        return functools.partial(self._call, name)

    # ---- one call -----------------------------------------------------------

    def _call(self, name: str, /, *args: Any, **kwargs: Any) -> Any:
        server = _Server.of(self._api, self._settings)
        with self._refresh_lock:  # the directive governs the first call; the pick serves the rest
            refresh, self._refresh = self._refresh, None
        pick = server.pick(refresh)
        with pick.lock:
            chosen = pick.chosen
        build = functools.partial(pick.adapter_for, self._api, settings=self._settings)
        call = _Call(server, pick, build, (name, args, kwargs))
        return call.run(chosen, rerun=False)


@dataclass
class _Call:
    """One port call: run on the chosen adapter, and once more after a switch when it may."""

    server: _Server
    pick: Pick
    build: Callable[[tuple[Any, ServerVersion | None]], Any]
    again: tuple[str, tuple[Any, ...], dict[str, Any]]
    #: The version the call first ran on, once a switch reruns it.
    was: ServerVersion | None = None

    def passed(self, error: ApiMismatch, chosen: tuple[Any, Any], log: CallLog) -> ApiMismatch:
        """``error`` passed through after the rerun, or with no switch to make."""
        was = chosen[1] if self.was is None else self.was
        rerun = self.was is not None
        return self.server.mismatch(self.pick, error, log=log, was=was, now=chosen[1], rerun=rerun)

    def run(self, chosen: tuple[Any, ServerVersion | None], *, rerun: bool) -> Any:
        name, args, kwargs = self.again
        log = CallLog()
        try:
            with logging_into(self.server.invocation, log):
                result = getattr(self.build(chosen), name)(*args, **kwargs)
        except ApiMismatch as exc:
            if exc._passed:  # a port call inside this one already handled it
                raise
            if rerun:
                raise self.passed(exc, chosen, log) from exc
            return self.server.after_mismatch(self, chosen, exc, log, handed=0)
        if isinstance(result, Iterator):
            return self.iterate(chosen, result, log, rerun=rerun)
        return result

    def iterate(
        self,
        chosen: tuple[Any, ServerVersion | None],
        items: Iterator[Any],
        log: CallLog,
        *,
        rerun: bool,
    ) -> Iterator[Any]:
        """``items``, each fetched under the call's log; a mismatch meets the same test."""
        handed = 0
        try:
            while True:
                try:
                    with logging_into(self.server.invocation, log):
                        item = next(items)
                except StopIteration:
                    return
                except ApiMismatch as exc:
                    if exc._passed:
                        raise
                    if rerun:  # already the rerun: it passes through
                        raise self.passed(exc, chosen, log) from exc
                    yield from self.server.after_mismatch(self, chosen, exc, log, handed=handed)
                    return
                handed += 1
                yield item
        finally:
            close = getattr(items, "close", None)
            if close is not None:
                close()


@dataclass(frozen=True)
class _Server:
    """One block's server for one call: the settings, URL and override it names, in this run."""

    api: ServerApi[Any, Any, Any]
    settings: BaseModel
    run: Run
    key: CredentialKey
    url: str
    override: ServerVersion | None

    @classmethod
    def of(cls, api: ServerApi[Any, Any, Any], settings: BaseModel) -> _Server:
        """The server ``settings`` name; the plugin's not-set-up error when none is set."""
        from untaped.sdk.network.http import _not_set_up  # noqa: PLC0415 - httpx's cost

        run = current_run()
        key = value_in(api.server, settings)
        url = None if key is None else key_origin(key)
        if key is None or not url:
            raise _not_set_up(settings, section=api.plugin)
        override = value_in(api.override, settings)
        return cls(api, settings, run, key, url.rstrip("/"), override)

    # ---- names --------------------------------------------------------------

    @property
    def plugin(self) -> str:
        return self.api.plugin

    @property
    def named(self) -> str:
        """``the jira server at https://jira.example.com``."""
        return f"the {self.plugin} server at {self.url}"

    @property
    def distribution(self) -> str:
        """``untaped-jira``: whose support a message speaks of (the plugin's name without one)."""
        return owner(self.plugin) or self.plugin

    @property
    def setting(self) -> str:
        """The override's full key: ``jira.server_version``."""
        return setting_name(self.api.override)

    @property
    def invocation(self) -> CallLog:
        """What every port call of this invocation sent."""
        return invocation_log(self.run.composition)

    # ---- the pick -----------------------------------------------------------

    def pick(self, refresh: bool | None) -> Pick:
        """This server's pick, made now when there is none (or ``refresh`` asks to detect)."""
        api = self.api
        composition = self.run.composition
        memo = (id(composition), self.url, None if self.override is None else str(self.override))
        with api._picks_lock:
            pick = api._picks.get(memo)
            if pick is None:
                pick = api._picks[memo] = Pick()
                # a finished run's pick goes with it, closing the clients its adapters opened
                weakref.finalize(composition, api._picks.pop, memo, None)
        with pick.lock:  # --refresh detects once per invocation, however many ports ask
            again = refresh is True and self.override is None and pick.source != "detected"
            if pick.adapter is None or again:
                self._choose(pick, refresh)
        return pick

    def _choose(self, pick: Pick, refresh: bool | None) -> None:
        """Find the version, pick the first adapter serving it, or refuse."""
        source: Source
        if self.override is not None:
            version, source = self.override, "setting"
        else:
            version, source = self.detected(refresh)
        adapter = self.serving(version)
        if adapter is None and source == "cache" and refresh is not False:
            version, source = self.detected(True)  # never refused on a day-old answer
            adapter = self.serving(version)
        if adapter is None:
            raise self.refusal(version, source)
        self._adopt(pick, adapter, version, source)

    def serving(self, version: ServerVersion) -> Any:
        """The first adapter in declared order serving ``version``."""
        return next((each for each in self.api.adapters if each.serves(version)), None)

    def _adopt(self, pick: Pick, adapter: type, version: ServerVersion, source: Source) -> None:
        """Make ``adapter`` the pick; warn of its removal when this run found the version."""
        if (pick.adapter, pick.version) != (adapter, version):
            pick.announced = False  # a new switch: the next rerun says it
        pick.adapter, pick.version, pick.source = adapter, version, source
        mark = adapter.__dict__.get("stability")
        if isinstance(mark, Deprecated) and source != "cache":
            self._warn_deprecated(adapter, mark, version)

    # ---- detection ----------------------------------------------------------

    def detected(self, refresh: bool | None) -> tuple[ServerVersion, Source]:
        """The server's version from the cache, detected now when ``refresh`` or its age asks."""
        version_type = self.api._version_type

        def fill(key: str, handle: ProgressHandle) -> Filled[_Detected]:
            del key, handle
            return Filled(value=_Detected(version=str(self.detect())))

        served = self.api.cache.serve({self.url: self.named}, refresh=refresh, fill=fill)
        kept = served.value[self.url].version
        try:
            version = version_type.parse(kept)
        except ValueError as exc:
            if self.url in served.filled:
                raise  # the plugin's detect answered a version its own parse refuses
            if refresh is False:
                raise self.could_not_tell(f"the version kept for it ({kept!r}) {exc}") from exc
            return self.detected(True)  # kept by an older spelling: ask the server again
        return version, "detected" if self.url in served.filled else "cache"

    def detect(self) -> ServerVersion:
        """Ask the server which version it runs, through the plugin's ``detect``."""
        anonymous = self.api.credential.find(self.key) is None
        client = connect(self.api, self.settings, None)
        try:
            with recording_only(CallLog()):  # a detection's requests are no call's
                version: ServerVersion = self.api.detect(self.settings, client)
                return version
        except ApiMismatch as exc:
            raise self.could_not_tell(str(exc), anonymous=anonymous) from exc
        except HttpStatusError as exc:
            if exc.status_code not in (401, 403):
                raise self._while_detecting(exc) from exc
            if anonymous:
                raise self.could_not_tell(
                    f"it answered HTTP {exc.status_code}", anonymous=True
                ) from exc
            raise self._rejected(exc) from exc
        except UntapedError as exc:
            raise self._while_detecting(exc) from exc
        finally:
            client.close()

    # ---- errors -------------------------------------------------------------

    def could_not_tell(self, why: str, *, anonymous: bool = False) -> ConfigError:
        """``could not tell which version <server> runs``: the one error offering the override."""
        shape = key_shape(type(self.key))
        url = shape.token_url or (shape.urls[0] if shape.urls else None)
        where = setting_name(self.api.server) + ("" if url is None else f".{url}")
        recorded = next((each.recorded[0] for each in self.api.adapters if each.recorded), None)
        example = f'untaped config set {self.setting} "{recorded or "<version>"}"'
        login = f"run `untaped {self.api.credential.login}`, " if anonymous else ""
        return ConfigError(
            f"could not tell which version {self.named} runs: {why}",
            system=self.plugin,
            hint=f"{login}check {where}, or set {self.setting}, for example `{example}`",
            details={"server": self.url},
        )

    def _while_detecting(self, error: UntapedError) -> UntapedError:
        """``error`` worded as met while detecting, keeping its category."""
        fields = attribution(error)
        fields["details"] = {**error.details, "server": self.url}
        return UntapedError(f"while checking which version {self.named} runs: {error}", **fields)

    def _rejected(self, error: HttpStatusError) -> ConfigError:
        from untaped.sdk.network.http import rejected_token_error  # noqa: PLC0415

        return rejected_token_error(
            Service(type(self.settings)),
            f"{self.named} refused the {self.api.credential.label} while untaped checked which "
            f"version it runs (HTTP {error.status_code})",
            cause=error,
            field=self.api.credential.name,
        )

    def refusal(self, version: ServerVersion, source: Source) -> UnsupportedServer:
        """The exit-4 refusal of a version no adapter serves."""
        supported = [self._served_text(each) for each in self.api.adapters]
        by = f" (set by {self.setting})" if source == "setting" else ""
        hint = (
            f"{self.setting} is set; unset it to detect the version the server runs"
            if source == "setting"
            else "ask its administrator about an upgrade"
        )
        return UnsupportedServer(
            f"{self.distribution} supports {', '.join(supported)}; {self.named} runs {version}{by}",
            system=self.plugin,
            hint=hint,
            details={
                "server": self.url,
                "version": str(version),
                "source": source,
                "supported": supported,
            },
        )

    def _served_text(self, adapter: Any) -> str:
        """An adapter's first docstring line, with ``(until untaped-jira 13.0)`` when deprecated."""
        mark = adapter.__dict__.get("stability")
        if not isinstance(mark, Deprecated) or mark.removed_in is None:
            return summary_line(adapter)
        return f"{summary_line(adapter)} (until {removal_text(mark.removed_in, self.distribution)})"

    def _warn_deprecated(self, adapter: type, mark: Deprecated, version: ServerVersion) -> None:
        replacement = mark.replacement
        removal = removal_text(mark.removed_in, self.distribution)
        upgrade = "ask its administrator to upgrade" + (f" before {removal}" if removal else "")
        notify_deprecation(
            f"{self.plugin}.{self.api.name}.{adapter.__name__}",
            f"support for {summary_line(adapter)}",
            removed_in=mark.removed_in,
            owner=self.distribution,
            replacement=summary_line(replacement) if isinstance(replacement, type) else None,
            hint=f"{self.url} runs {version}; {upgrade}",
            plugin=self.plugin,
        )

    def mismatch(
        self,
        pick: Pick,
        error: ApiMismatch,
        *,
        log: CallLog,
        was: ServerVersion | None,
        now: ServerVersion | None,
        rerun: bool,
    ) -> ApiMismatch:
        """``error`` passed through: what answered ``now``, and how untaped did not expect it."""
        asked = "" if error.request is None else f" {error.request}"
        how = f" ({error.answer})" if error.answer is not None else f": {error!s}"
        hint = None
        if pick.source == "setting":
            hint = f"{self.setting} says the server runs {now}; unset it to detect it"
        passed = ApiMismatch(
            f"{now} answered{asked} in a way {self.distribution} does not expect{how}",
            request=error.request,
            answer=error.answer,
            system=self.plugin,
            hint=hint,
            details={
                "server": self.url,
                "was": str(was),
                "now": str(now),
                "source": pick.source,
                "written": log.wrote,
                "rerun": rerun,
            },
        )
        passed._passed = True
        return passed

    # ---- after a mismatch ---------------------------------------------------

    def after_mismatch(
        self,
        call: _Call,
        called: tuple[Any, ServerVersion | None],
        error: ApiMismatch,
        log: CallLog,
        *,
        handed: int,
    ) -> Any:
        """Re-detect once, switch when the server changed, and rerun when nothing happened."""
        pick, was = call.pick, called[1]
        with pick.lock:
            if pick.chosen == called:  # no other thread switched it since this call began
                if pick.source == "setting" or pick.redetected:
                    raise call.passed(error, called, log) from error
                pick.redetected = True
                version, _ = self.detected(True)
                if version == was:
                    raise call.passed(error, called, log) from error
                adapter = self.serving(version)
                if adapter is None:
                    raise self.refusal(version, "detected") from error
                self._adopt(pick, adapter, version, "detected")
            chosen = pick.chosen
        printed = self.run.notes.printed
        if log.wrote or printed or handed > 0:
            raise self._changed(was, chosen[1], written=log.wrote, printed=printed) from error
        with pick.lock:
            announce, pick.announced = not pick.announced, True
        if announce:
            self.run.diag.warning(
                f"{self.named} changed from {was} to {chosen[1]}; switched to its adapter"
            )
        call.was = was
        return call.run(chosen, rerun=True)

    def _changed(
        self, was: ServerVersion | None, now: ServerVersion | None, *, written: bool, printed: bool
    ) -> UntapedError:
        """The server changed under the call and it can't run again unseen: exit 1 or 5."""
        changed = f"{self.named} changed from {was} to {now}"
        earlier = not written and self.invocation.wrote
        details = {
            "server": self.url,
            "was": str(was),
            "now": str(now),
            "source": "detected",
            "written": written or earlier,
            "rerun": False,
        }
        if written or earlier:
            what = "the request" if written else "an earlier request of this command"
            return UntapedError(
                f"{changed}; {what} may have taken effect",
                category=ErrorCategory.FAILED,
                system=self.plugin,
                hint="check what the command changed before running it again",
                details=details,
            )
        after = "output was printed" if printed else "the command read part of the answer"
        return UntapedError(
            f"{changed} after {after}; nothing took effect",
            category=ErrorCategory.UNAVAILABLE,
            system=self.plugin,
            hint="run the command again",
            details=details,
        )
