"""Reusable doctor-check factories for capabilities.

Each factory returns a :class:`DoctorCheck` a capability lists in its
``SPEC.doctor_checks``. :func:`executable_check` and :func:`connection_check`
read the validated settings snapshot and the local machine only: no network
I/O and no ``token_command`` runs. :func:`online_check` is the one online
check: ``doctor --online`` runs it to contact the configured service.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator

from untaped.auth import describe_token_source
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
    of the pair is a warning. ``token_command`` is reported, never run.
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
        return DoctorResult(id=check_id, ok=True, detail=f"{base_url}; token from {source}")

    return DoctorCheck(id=check_id, title=f"{section} connection settings", run=run)


def online_check(
    check_id: str,
    *,
    section: str,
    probe: Callable[[], str],
    title: str | None = None,
) -> DoctorCheck:
    """Contact ``section``'s service through ``probe`` (``doctor --online`` only).

    ``probe`` is the capability's own authenticated call (``whoami``-style),
    run against the active profile; it returns the pass detail and raises
    :class:`UntapedError` on failure. A section without a token source whose
    ``base_url`` is unset (or the model default) is not configured and passes
    without probing. A failure
    names the command that fixes it: a new token for a rejected or missing
    one, ``http.ca_bundle`` for a TLS failure, otherwise the ``base_url``.
    """

    def run(ctx: CapabilityContext) -> DoctorResult:
        settings = ctx.settings
        if settings is None:
            return DoctorResult(id=check_id, ok=True, detail="skipped: settings are invalid")
        base_url = str(getattr(settings, "base_url", None) or "").strip()
        source = describe_token_source(settings, section=section)
        field = type(settings).model_fields.get("base_url")
        default_url = field.default if field is not None else None
        if source is None and (not base_url or base_url == default_url):
            # Nothing the user set: a built-in default URL alone is not a setup.
            return DoctorResult(id=check_id, ok=True, detail="not configured")
        url_fix = f"config set {section}.base_url URL"
        if not base_url:
            detail = f"{section}.base_url is not set"
            return DoctorResult(id=check_id, ok=False, detail=detail, fix=url_fix)
        try:
            detail = probe()
        except UntapedError as exc:
            message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return DoctorResult(
                id=check_id,
                ok=False,
                detail=message,
                fix=_online_fix(exc, section=section, has_token=source is not None),
            )
        return DoctorResult(id=check_id, ok=True, detail=detail)

    return DoctorCheck(id=check_id, title=title or f"{section} API reachable", run=run, online=True)


def _online_fix(exc: BaseException, *, section: str, has_token: bool) -> str:
    chain = list(_causes(exc))
    rejected = any(
        isinstance(error, HttpError) and error.status_code in (401, 403) for error in chain
    )
    if rejected or not has_token:
        return f"config set {section}.token --prompt"
    if any(
        isinstance(error, HttpTransportError) and "certificate" in str(error).lower()
        for error in chain
    ):
        return "config set http.ca_bundle PATH"
    return f"config set {section}.base_url URL"


def _causes(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


__all__ = ["connection_check", "executable_check", "online_check"]
