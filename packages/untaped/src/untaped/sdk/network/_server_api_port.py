"""The port's proxy: picking an adapter, refusing a server, and switching after a mismatch.

``ServerApi.port(settings)`` returns a :class:`Port`. Reading one of the
port's members returns a function and sends nothing; calling it checks the
plugin is set up, finds the version and picks the adapter (:class:`Pick`),
then forwards the call. Only the port's members are reachable.

The pick is a memo on the block, keyed by the run's composition (one per
invocation, which its child runs and threads share), the server URL and the
override's text, under a lock: ``Parallel.map`` threads share one pick, and
a child run on another server or override gets its own. No run state is
added; a finished run's picks are dropped. Detection is the block's cache,
keyed by the URL alone.

After an :class:`~untaped.sdk.network.server_api.ApiMismatch` from a port
call, core re-detects once per pick (never under the override): the same
version passes the mismatch through (exit 1); another served version
switches the pick, and the call runs again on the new adapter when no write
went through during it and nothing was printed or handed to the command;
otherwise it fails with exit 1 (a write went through) or 5 (output already
printed). A version nothing serves is the exit-4 refusal.
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
from untaped.sdk.network._server_api_calls import CallLog, logging_into, record
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
    """One server's pick in this process: the adapter serving it, and what decided it."""

    composition: weakref.ref[object]
    lock: threading.RLock = field(default_factory=threading.RLock)
    adapter: Any = None
    version: ServerVersion | None = None
    source: Source = "detected"
    #: Whether a mismatch already re-detected this server: a later one passes through.
    redetected: bool = False
    #: The version before the re-detection switched the pick, for the rerun's wording.
    was: ServerVersion | None = None


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
        adapter = pick.adapter
        log = CallLog()
        try:
            with logging_into(log):
                result = getattr(adapter, name)(*args, **kwargs)
        except ApiMismatch as exc:
            return server.after_mismatch(
                pick, adapter, exc, log, handed=0, again=(name, args, kwargs)
            )
        if isinstance(result, Iterator):
            return server.iterate(pick, adapter, result, log, again=(name, args, kwargs))
        return result


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

    # ---- the pick -----------------------------------------------------------

    def pick(self, refresh: bool | None) -> Pick:
        """This server's pick, made now when there is none (or ``refresh`` asks to detect)."""
        api = self.api
        composition = self.run.composition
        memo = (id(composition), self.url, None if self.override is None else str(self.override))
        with api._picks_lock:
            pick = api._picks.get(memo)
            if pick is None or pick.composition() is not composition:
                gone = [each for each, held in api._picks.items() if held.composition() is None]
                for each in gone:
                    del api._picks[each]  # a finished run's
                pick = api._picks[memo] = Pick(composition=weakref.ref(composition))
        with pick.lock:
            if pick.adapter is None or (refresh is True and self.override is None):
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
        if adapter is None and source == "cache":
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
        built = adapter(self.settings, version)
        built._connector = self._connector
        pick.adapter, pick.version, pick.source = built, version, source
        mark = adapter.__dict__.get("stability")
        if isinstance(mark, Deprecated) and source != "cache":
            self._warn_deprecated(adapter, mark, version)

    def _connector(self, headers: Mapping[str, str] | None) -> HttpClient:
        """A client for the server, as ``ctx.http.client`` builds it, recording what it sends."""
        # Core reads the key's TLS field by its type, as ``ctx.http.client`` callers name it.
        shape = key_shape(type(self.key))
        verify = True if shape.tls is None else getattr(self.key, shape.tls)
        client = self.run.http.client(
            self.settings,
            base_url=self.url,
            bearer=self.api.credential.find(self.key),
            verify=verify,
            headers=headers,
        )
        client._record = record
        return client

    # ---- detection ----------------------------------------------------------

    def detected(self, refresh: bool | None) -> tuple[ServerVersion, Source]:
        """The server's version from the cache, detected now when ``refresh`` or its age asks."""
        version_type = self.api._version_type

        def fill(key: str, handle: ProgressHandle) -> Filled[_Detected]:
            del key, handle
            return Filled(value=_Detected(version=str(self.detect())))

        served = self.api.cache.serve({self.url: self.named}, refresh=refresh, fill=fill)
        try:
            version = version_type.parse(served.value[self.url].version)
        except ValueError:
            if refresh is True or self.url in served.filled:
                raise
            return self.detected(True)  # kept by an older spelling: ask the server again
        return version, "detected" if self.url in served.filled else "cache"

    def detect(self) -> ServerVersion:
        """Ask the server which version it runs, through the plugin's ``detect``."""
        anonymous = self.api.credential.find(self.key) is None
        client = self._connector(None)
        try:
            with logging_into(CallLog()):
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

    def could_not_tell(self, why: str, *, anonymous: bool) -> ConfigError:
        """``could not tell which version <server> runs``: the one error offering the override."""
        example = f'untaped config set {self.setting} "<version>"'
        login = f"`untaped {self.api.credential.login}`, or " if anonymous else ""
        return ConfigError(
            f"could not tell which version {self.named} runs: {why}",
            system=self.plugin,
            hint=f"check the server's URL; {login}say it with `{example}`",
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

    def mismatch(self, pick: Pick, error: ApiMismatch, **details: object) -> ApiMismatch:
        """``error`` passed through: what answered, and how untaped did not expect it."""
        asked = "" if error.request is None else f" {error.request}"
        how = f" ({error.answer})" if error.answer is not None else f": {error!s}"
        hint = None
        if pick.source == "setting":
            hint = f"{self.setting} says the server runs {pick.version}; unset it to detect it"
        return ApiMismatch(
            f"{pick.version} answered{asked} in a way {self.distribution} does not expect{how}",
            request=error.request,
            answer=error.answer,
            system=self.plugin,
            hint=hint,
            details={
                "server": self.url,
                "version": str(pick.version),
                "source": pick.source,
                **details,
            },
        )

    # ---- after a mismatch ---------------------------------------------------

    def after_mismatch(
        self,
        pick: Pick,
        called: object,
        error: ApiMismatch,
        log: CallLog,
        *,
        handed: int,
        again: tuple[str, tuple[Any, ...], dict[str, Any]],
    ) -> Any:
        """Re-detect once, switch when the server changed, and rerun when nothing happened."""
        with pick.lock:
            if pick.adapter is called:
                if pick.source == "setting" or pick.redetected:
                    raise self.mismatch(pick, error) from error
                pick.redetected = True
                was = pick.version
                assert was is not None  # a pick that served a call has its version
                version, _ = self.detected(True)
                if version == was:
                    raise self.mismatch(pick, error) from error
                adapter = self.serving(version)
                if adapter is None:
                    raise self.refusal(version, "detected") from error
                self._adopt(pick, adapter, version, "detected")
                pick.was = was
                warned = False
            else:
                warned = True  # another thread switched the pick and said so
        written = log.wrote
        printed = self.run.notes.printed or handed > 0
        if written or printed:
            raise self._changed(pick, written=written) from error
        if not warned:
            self.run.diag.warning(
                f"{self.named} changed from {pick.was} to {pick.version}; switched to its adapter"
            )
        name, args, kwargs = again
        log = CallLog()
        try:
            with logging_into(log):
                result = getattr(pick.adapter, name)(*args, **kwargs)
        except ApiMismatch as exc:
            raise self.mismatch(pick, exc, rerun=True) from exc
        if isinstance(result, Iterator):
            return self.iterate(pick, pick.adapter, result, log, again=None)
        return result

    def iterate(
        self,
        pick: Pick,
        adapter: object,
        items: Iterator[Any],
        log: CallLog,
        *,
        again: tuple[str, tuple[Any, ...], dict[str, Any]] | None,
    ) -> Iterator[Any]:
        """``items``, each fetched under the call's record; a mismatch meets the same test."""
        handed = 0
        while True:
            try:
                with logging_into(log):
                    item = next(items)
            except StopIteration:
                return
            except ApiMismatch as exc:
                if again is None:  # already the rerun: it passes through
                    raise self.mismatch(pick, exc, rerun=True) from exc
                rerun = self.after_mismatch(pick, adapter, exc, log, handed=handed, again=again)
                yield from rerun
                return
            handed += 1
            yield item

    def _changed(self, pick: Pick, *, written: bool) -> UntapedError:
        """The server changed under the call and it can't run again unseen: exit 1 or 5."""
        changed = f"{self.named} changed from {pick.was} to {pick.version}"
        details = {
            "server": self.url,
            "was": str(pick.was),
            "now": str(pick.version),
            "source": "detected",
            "written": written,
            "rerun": False,
        }
        if written:
            return UntapedError(
                f"{changed}; the request may have taken effect",
                category=ErrorCategory.FAILED,
                system=self.plugin,
                hint="check what the command changed before running it again",
                details=details,
            )
        return UntapedError(
            f"{changed} after output was printed; nothing took effect",
            category=ErrorCategory.UNAVAILABLE,
            system=self.plugin,
            hint="run the command again",
            details=details,
        )
