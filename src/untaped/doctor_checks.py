"""Reusable offline doctor-check factories for capabilities.

Each factory returns a :class:`DoctorCheck` a capability lists in its
``SPEC.doctor_checks``. The checks read the validated settings snapshot and
the local machine only: no network I/O and no ``token_command`` runs.
"""

from __future__ import annotations

import shutil

from untaped.auth import describe_token_source
from untaped.capabilities.registry import CapabilityContext, DoctorCheck, DoctorResult


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


__all__ = ["connection_check", "executable_check"]
