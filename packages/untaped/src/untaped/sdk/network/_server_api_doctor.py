"""The server API block's doctor row, ``<plugin>.server-api``: which server, served by what.

Offline the row reads what is known without a request: the override, or
the version detection cached for the server; under ``--online`` it detects
again and keeps the answer, as a command would. A version no adapter serves
fails with the refusal's words; an adapter marked deprecated warns, always;
an override the server contradicts (``--online`` only) warns with the
command unsetting it, and when detection can't answer the override does.
Nothing detected yet passes: the first command detects it. Reaching the
server is the plugin's own online check's question, so a server this row
can't reach is a warning (with the override set, the override's row).
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

from untaped.sdk._keys import value_in
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.health.doctor import DoctorResult
from untaped.sdk.marks import Deprecated
from untaped.sdk.network._server_api_port import _Server
from untaped.sdk.network.credentials import key_origin
from untaped.sdk.network.server_api import _Detected
from untaped.sdk.storage._cache_files import utcnow
from untaped.sdk.storage.cache import Filled
from untaped.sdk.words import ago_text

if TYPE_CHECKING:
    from untaped.sdk.health.doctor import PluginContext
    from untaped.sdk.network.server_api import ServerApi, ServerVersion
    from untaped.sdk.progress import ProgressHandle


def server_api_row(api: ServerApi[Any, Any, Any], ctx: PluginContext) -> DoctorResult:
    """The row for ``ctx``'s settings: offline from what is known, online from the server."""
    settings = ctx.settings
    if settings is None:
        return DoctorResult(ok=True, detail="skipped: settings are invalid")
    key = value_in(api.server, settings)
    if key is None or not key_origin(key):
        return DoctorResult(ok=True, detail="skipped: not set up")  # setup's row says what
    server = _Server.of(api, settings)
    override = server.override
    if not ctx.online:
        return _cached(server) if override is None else _served(server, override, _set(server))
    try:
        detected = server.detect()
    except UntapedError as exc:
        if override is not None:  # what commands use: the setting answers what detection can't
            return _served(server, override, _set(server))
        if isinstance(exc, ConfigError):  # could not tell, or the token was refused
            return DoctorResult(ok=False, detail=_text(exc))
        return DoctorResult(ok=True, warn=True, detail=f"not checked: {exc}")
    with suppress(UntapedError):  # best effort: the row reports what it detected either way
        _remember(server, detected)
    if override is None:
        return _served(server, detected, f"{detected}, detected now")
    if detected != override:
        return DoctorResult(
            ok=True,
            warn=True,
            detail=f"{_set(server)}, but the server runs {detected}",
            fix=f"config unset {server.setting}",
        )
    return _served(server, override, _set(server))


def _cached(server: _Server) -> DoctorResult:
    """Offline with no override: the version detection cached for the server, without a request."""
    cached = server.api.cache.cached(server.url)
    version = None
    # A version kept by an older spelling reads as none: the next command detects it again.
    with suppress(ValueError):
        version = None if cached is None else server.api._version_type.parse(cached.value.version)
    if cached is None or version is None:
        return DoctorResult(ok=True, detail="not detected yet: the first command detects it")
    at = cached.freshness[server.url].refreshed_at  # set: cached() serves refreshed keys only
    when = "" if at is None else f" {ago_text(at, utcnow())}"
    return _served(server, version, f"{version}, detected{when}")


def _remember(server: _Server, version: ServerVersion) -> None:
    """Keep what ``--online`` detected, as a command's detection would."""

    def fill(key: str, handle: ProgressHandle) -> Filled[_Detected]:
        del key, handle
        return Filled(value=_Detected(version=str(version)))

    server.api.cache.serve({server.url: server.named}, refresh=True, fill=fill)


def _set(server: _Server) -> str:
    """``Toy 1.4 (set by toy.server_version)``: the override, said with its setting."""
    return f"{server.override} (set by {server.setting})"


def _text(error: ConfigError) -> str:
    return str(error) if error.hint is None else f"{error}; {error.hint}"


def _served(server: _Server, version: ServerVersion, said: str) -> DoctorResult:
    """``said`` and the adapter serving ``version``; the refusal when none does."""
    adapter = server.serving(version)
    if adapter is None:
        refused = server.refusal(version, "setting" if server.override is not None else "detected")
        return DoctorResult(ok=False, detail=str(refused))
    deprecated = isinstance(adapter.__dict__.get("stability"), Deprecated)
    return DoctorResult(ok=True, warn=deprecated, detail=f"{said} · {server.served_text(adapter)}")
