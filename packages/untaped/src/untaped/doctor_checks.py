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

from untaped.auth import describe_token_source, token_alternatives
from untaped.capabilities.registry import CapabilityContext, DoctorCheck, DoctorResult
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, HttpError, HttpTransportError, UntapedError
from untaped.settings import active_settings_layout, resolve_config_path


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
    config file (``<section>.token``): its fix is ``auth migrate`` when the
    section takes a ``token_command``, else the alternatives are named.
    ``token_command`` is reported, never run.
    """

    def run(ctx: CapabilityContext) -> DoctorResult:
        settings = ctx.settings
        if settings is None:
            return DoctorResult(id=check_id, ok=True, detail="skipped: settings are invalid")
        base_url = str(getattr(settings, "base_url", None) or "").strip()
        source = describe_token_source(settings, section=section, ambient=bool(base_url))
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
        if _stored_token(section):
            plaintext = (
                f"{base_url}; {section}.token is stored in plain text in "
                f"{resolve_config_path().name}"
            )
            if _takes_command(settings):
                return DoctorResult(
                    id=check_id, ok=True, warn=True, detail=plaintext, fix="auth migrate"
                )
            alternatives = (
                token_alternatives(settings, section=section)
                or f"$UNTAPED_{section.upper()}__TOKEN"
            )
            return DoctorResult(
                id=check_id,
                ok=True,
                warn=True,
                detail=f"{plaintext}; use {alternatives} instead",
            )
        return DoctorResult(id=check_id, ok=True, detail=f"{base_url}; token from {source}")

    return DoctorCheck(id=check_id, title=f"{section} connection settings", run=run)


def _stored_token(section: str) -> bool:
    """Whether the config file itself sets ``<section>.token`` for the resolved profile.

    Read from the file, not the validated settings: an ``UNTAPED_*`` override
    neither hides a token stored in the file nor counts as one.
    """
    try:
        node = active_settings_layout().effective(read_config_dict()).get(section)
    except ConfigError:
        return False
    token = node.get("token") if isinstance(node, dict) else None
    return isinstance(token, str) and bool(token.strip())


def service_configured(settings: BaseModel, *, section: str) -> bool:
    """Whether ``section`` is set up: a token source, or a ``base_url`` of the user's own.

    A model's built-in default URL (GitHub's, say) alone does not count, and
    a conventional token variable counts only alongside a ``base_url``: it
    is not tied to a profile.
    """
    base_url = str(getattr(settings, "base_url", None) or "").strip()
    if describe_token_source(settings, section=section, ambient=bool(base_url)) is not None:
        return True
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
        url_fix = f"config set {section}.base_url <URL>"
        if not str(getattr(settings, "base_url", None) or "").strip():
            detail = f"{section}.base_url is not set"
            return DoctorResult(id=check_id, ok=False, detail=detail, fix=url_fix)
        has_token = describe_token_source(settings, section=section) is not None
        from untaped.http import quick_probe  # noqa: PLC0415 - keep SPEC imports light

        try:
            with quick_probe():
                detail = probe()
        except UntapedError as exc:
            detail = _first_line(exc) or type(exc).__name__
            token_fix = _token_fix(section, settings)
            fix = _online_fix(exc, section=section, has_token=has_token, token_fix=token_fix)
            alternatives = token_alternatives(settings, section=section)
            if fix == token_fix and alternatives:
                detail = f"{detail} (the token can also come from {alternatives})"
            return DoctorResult(id=check_id, ok=False, detail=detail, fix=fix)
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


def _takes_command(settings: BaseModel) -> bool:
    return "token_command" in type(settings).model_fields


def _token_fix(section: str, settings: BaseModel) -> str:
    if _takes_command(settings):
        return f"auth set {section}"
    return f"config set {section}.token --prompt"


def _online_fix(exc: BaseException, *, section: str, has_token: bool, token_fix: str) -> str:
    chain = list(_causes(exc))
    rejected = any(
        isinstance(error, HttpError) and error.status_code in (401, 403) for error in chain
    )
    if rejected:
        return token_fix
    if any(
        isinstance(error, ssl.SSLCertVerificationError)
        and getattr(error, "verify_code", None) != _HOSTNAME_MISMATCH
        for error in chain
    ):
        return "config set http.ca_bundle <PATH>"
    # An unreachable service is a URL problem even when no token is set yet.
    if not has_token and not any(isinstance(error, HttpTransportError) for error in chain):
        return token_fix
    return f"config set {section}.base_url <URL>"


def _causes(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


__all__ = ["connection_check", "executable_check", "online_check", "service_configured"]
