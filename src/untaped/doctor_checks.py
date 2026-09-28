"""Reusable doctor-check factories for capabilities.

Each factory returns a :class:`DoctorCheck` a capability lists in its
``SPEC.doctor_checks``. :func:`executable_check` and :func:`connection_check`
read the validated settings snapshot and the local machine only: no network
I/O and no ``token_command`` runs. :func:`online_check` is the one online
check: ``doctor --online`` runs it to contact the configured service.
"""

from __future__ import annotations

import shutil
import ssl
from collections.abc import Callable, Iterator

from pydantic import BaseModel

from untaped.auth import TokenSources, describe_token_source
from untaped.capabilities.registry import CapabilityContext, DoctorCheck, DoctorResult
from untaped.errors import HttpError, HttpTransportError, UntapedError


def executable_check(check_id: str, program: str, *, purpose: str) -> DoctorCheck:
    """Warn when ``program`` is not on ``PATH``; ``purpose`` says what needs it."""

    def run(_ctx: CapabilityContext) -> DoctorResult:
        found = shutil.which(program)
        if found is None:
            return DoctorResult(
                id=check_id,
                ok=True,
                warn=True,
                detail=f"`{program}` not found on PATH; {purpose} will fail",
            )
        return DoctorResult(id=check_id, ok=True, detail=found)

    return DoctorCheck(id=check_id, title=f"{program} on PATH", run=run)


def connection_check(check_id: str, *, section: str) -> DoctorCheck:
    """Check the resolved profile's ``<section>.base_url`` and token source.

    A section with neither is simply unused and passes; one with only half
    of the pair is a warning, and so is a token stored in plain text in the
    config file (``<section>.token``), which names ``token_command`` and an
    environment variable instead. ``token_command`` is reported, never run.
    """

    def run(ctx: CapabilityContext) -> DoctorResult:
        settings = ctx.settings
        if settings is None:
            return DoctorResult(id=check_id, ok=True, detail="skipped: settings are invalid")
        base_url = str(getattr(settings, "base_url", None) or "").strip()
        source = describe_token_source(settings, section=section)
        if not base_url and source is None:
            return DoctorResult(id=check_id, ok=True, detail="not configured")
        missing = [
            f"{section}.{name}"
            for name, present in (("base_url", bool(base_url)), ("token", source is not None))
            if not present
        ]
        if missing:
            return DoctorResult(
                id=check_id,
                ok=True,
                warn=True,
                detail=(
                    f"{' and '.join(missing)} not configured; "
                    f"{section} commands that call the API will fail"
                ),
            )
        if source == f"{section}.token":
            return DoctorResult(
                id=check_id,
                ok=True,
                warn=True,
                detail=(
                    f"{base_url}; token from {source}, stored in plain text in config.yml; "
                    f"use {_token_alternatives(settings, section=section)} instead"
                ),
            )
        return DoctorResult(id=check_id, ok=True, detail=f"{base_url}; token from {source}")

    return DoctorCheck(id=check_id, title=f"{section} connection settings", run=run)


def _token_alternatives(settings: BaseModel, *, section: str) -> str:
    """Name the keep-it-out-of-config.yml token sources ``section`` accepts."""
    sources = getattr(type(settings), "token_sources", None)
    env = sources.env if isinstance(sources, TokenSources) else ()
    names = [f"{section}.token_command"] if "token_command" in type(settings).model_fields else []
    names.append(f"${env[0] if env else f'UNTAPED_{section.upper()}__TOKEN'}")
    return " or ".join(names)


def service_configured(settings: BaseModel, *, section: str) -> bool:
    """Whether ``section`` is set up: a token source, or a ``base_url`` of the user's own.

    A model's built-in default URL (GitHub's, say) alone does not count.
    """
    if describe_token_source(settings, section=section) is not None:
        return True
    base_url = str(getattr(settings, "base_url", None) or "").strip()
    field = type(settings).model_fields.get("base_url")
    return bool(base_url) and (field is None or base_url != field.default)


def online_check(
    check_id: str,
    *,
    section: str,
    probe: Callable[[], str],
    title: str | None = None,
) -> DoctorCheck:
    """Contact ``section``'s service through ``probe`` (``doctor --online`` only).

    ``probe`` is the capability's own authenticated call (``whoami``-style),
    run against the active profile inside :func:`untaped.http.quick_probe`
    (no retries, a short timeout); it returns the pass detail and raises on
    failure. A section that is not :func:`service_configured` passes
    without probing. A failure keeps one line of its message and names the
    command that fixes it: a new token for a rejected or missing one,
    ``http.ca_bundle`` for an untrusted certificate, otherwise the
    ``base_url``.
    """

    def run(ctx: CapabilityContext) -> DoctorResult:
        settings = ctx.settings
        if settings is None:
            return DoctorResult(id=check_id, ok=True, detail="skipped: settings are invalid")
        if not service_configured(settings, section=section):
            return DoctorResult(id=check_id, ok=True, detail="not configured")
        url_fix = f"config set {section}.base_url URL"
        if not str(getattr(settings, "base_url", None) or "").strip():
            detail = f"{section}.base_url is not set"
            return DoctorResult(id=check_id, ok=False, detail=detail, fix=url_fix)
        has_token = describe_token_source(settings, section=section) is not None
        from untaped.http import quick_probe  # noqa: PLC0415 - keep SPEC imports light

        try:
            with quick_probe():
                detail = probe()
        except UntapedError as exc:
            return DoctorResult(
                id=check_id,
                ok=False,
                detail=_first_line(exc) or type(exc).__name__,
                fix=_online_fix(exc, section=section, has_token=has_token),
            )
        except Exception as exc:
            # Not a service error (e.g. an unexpected response shape): keep one
            # line, never the response body a validation dump would carry.
            detail = f"{type(exc).__name__}: {_first_line(exc)}".rstrip(": ")
            return DoctorResult(id=check_id, ok=False, detail=detail, fix=url_fix)
        return DoctorResult(id=check_id, ok=True, detail=detail)

    return DoctorCheck(id=check_id, title=title or f"{section} API reachable", run=run, online=True)


#: OpenSSL's verify code for a certificate that does not match the host name.
_HOSTNAME_MISMATCH = 62


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else ""


def _online_fix(exc: BaseException, *, section: str, has_token: bool) -> str:
    chain = list(_causes(exc))
    rejected = any(
        isinstance(error, HttpError) and error.status_code in (401, 403) for error in chain
    )
    if rejected:
        return f"config set {section}.token --prompt"
    if any(
        isinstance(error, ssl.SSLCertVerificationError)
        and getattr(error, "verify_code", None) != _HOSTNAME_MISMATCH
        for error in chain
    ):
        return "config set http.ca_bundle PATH"
    # An unreachable service is a URL problem even when no token is set yet.
    if not has_token and not any(isinstance(error, HttpTransportError) for error in chain):
        return f"config set {section}.token --prompt"
    return f"config set {section}.base_url URL"


def _causes(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


__all__ = ["connection_check", "executable_check", "online_check", "service_configured"]
