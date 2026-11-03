"""The server API block's doctor row, ``<plugin>.server-api``: which server, served by what.

Offline the row reads what is known without a request: the override, or
the version detection cached for the server; under ``--online`` it detects
again. A version no adapter serves fails with the refusal's words; an
adapter marked deprecated warns, always; an override the server
contradicts warns with the command unsetting it. Nothing detected yet
passes: the first command detects it. Reaching the server is the plugin's
own online check's question, so a server this row can't reach is a warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from untaped.sdk._keys import value_in
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.health.doctor import DoctorResult
from untaped.sdk.marks import Deprecated
from untaped.sdk.network._server_api_port import _Server
from untaped.sdk.network.credentials import key_origin
from untaped.sdk.storage._cache_files import utcnow
from untaped.sdk.words import ago_text

if TYPE_CHECKING:
    from untaped.sdk.health.doctor import PluginContext
    from untaped.sdk.network.server_api import ServerApi, ServerVersion


def server_api_row(api: ServerApi[Any, Any, Any], ctx: PluginContext) -> DoctorResult:
    """The row for ``ctx``'s settings: offline from what is known, online from the server."""
    settings = ctx.settings
    if settings is None:
        return DoctorResult(ok=True, detail="skipped: settings are invalid")
    key = value_in(api.server, settings)
    if key is None or not key_origin(key):
        return DoctorResult(ok=True, detail="skipped: not set up")  # setup's row says what
    server = _Server.of(api, settings)
    found = _found(server, online=ctx.online)
    if isinstance(found, DoctorResult):
        return found
    detected, when = found
    if server.override is not None:
        return _overridden(server, server.override, detected)
    if detected is None:
        return DoctorResult(ok=True, detail="not detected yet: the first command detects it")
    return _served(server, detected, f"{detected}, {when}")


def _found(server: _Server, *, online: bool) -> tuple[ServerVersion | None, str] | DoctorResult:
    """The version detected (now under ``online``, else as cached), and when; or the row."""
    if not online:
        cached = server.api.cache.cached(server.url)
        if cached is None:
            return None, ""
        at = cached.freshness[server.url].refreshed_at
        when = "detected" if at is None else f"detected {ago_text(at, utcnow())}"
        try:
            return server.api._version_type.parse(cached.value.version), when
        except ValueError:
            return None, ""  # kept by an older spelling: the next command detects it again
    try:
        version, _ = server.detected(True)
    except ConfigError as exc:  # could not tell, or the token was refused
        return DoctorResult(ok=False, detail=str(exc) if exc.hint is None else f"{exc}; {exc.hint}")
    except UntapedError as exc:
        return DoctorResult(ok=True, warn=True, detail=f"not checked: {exc}")
    return version, "detected now"


def _overridden(
    server: _Server, override: ServerVersion, detected: ServerVersion | None
) -> DoctorResult:
    """The override's row: as any version's, unless the server says otherwise."""
    said = f"{override} (set by {server.setting})"
    if detected is not None and detected != override:
        return DoctorResult(
            ok=True,
            warn=True,
            detail=f"{said}, but the server runs {detected}",
            fix=f"config unset {server.setting}",
        )
    return _served(server, override, said)


def _served(server: _Server, version: ServerVersion, said: str) -> DoctorResult:
    """``said`` and the adapter serving ``version``; the refusal when none does."""
    adapter = server.serving(version)
    if adapter is None:
        refused = server.refusal(version, "setting" if server.override is not None else "detected")
        return DoctorResult(ok=False, detail=str(refused))
    deprecated = isinstance(adapter.__dict__.get("stability"), Deprecated)
    return DoctorResult(ok=True, warn=deprecated, detail=f"{said} · {server.served_text(adapter)}")
