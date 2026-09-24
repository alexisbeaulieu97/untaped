"""Doctor checks the ansible capability contributes to root ``untaped doctor``.

Checks run offline against the validated ``ansible`` profile section only.
"""

from __future__ import annotations

from untaped.capabilities.ansible.settings import AnsibleSettings
from untaped.capability_api import (
    CapabilityContext,
    DoctorCheck,
    DoctorResult,
    executable_check,
)

DEPRECATED_SETTINGS_ID = "ansible.deprecated-settings"


def check_deprecated_settings(ctx: CapabilityContext) -> DoctorResult:
    """Warn (without failing) while a deprecated, ignored key is still set."""
    settings = ctx.settings
    if isinstance(settings, AnsibleSettings) and settings.freshness_ttl is not None:
        return DoctorResult(
            id=DEPRECATED_SETTINGS_ID,
            ok=True,
            warn=True,
            detail=(
                "ansible.freshness_ttl is deprecated and ignored; remove it with "
                "`untaped config unset ansible.freshness_ttl`"
            ),
        )
    return DoctorResult(id=DEPRECATED_SETTINGS_ID, ok=True, detail="no deprecated settings")


DOCTOR_CHECKS = (
    DoctorCheck(
        id=DEPRECATED_SETTINGS_ID,
        title="deprecated settings",
        run=check_deprecated_settings,
    ),
    executable_check("ansible.git", "git", purpose="git-backed source refresh"),
)
