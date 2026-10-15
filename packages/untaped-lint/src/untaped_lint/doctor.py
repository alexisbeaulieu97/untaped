"""The lint plugin's doctor row: every id the profile's ``lint.ignore`` names is installed."""

from __future__ import annotations

from untaped.sdk import DoctorResult, PluginContext

IGNORE_IDS = "lint.ignore-ids"


def ignore_ids_check(ctx: PluginContext) -> DoctorResult:
    """``lint.ignore-ids``: warns about a ``lint.ignore`` id no installed rule declares."""
    from untaped_lint.application.engine import loaded_rules  # noqa: PLC0415
    from untaped_lint.domain.selection import Resolver, parse_suppression  # noqa: PLC0415
    from untaped_lint.settings import LintSettings  # noqa: PLC0415

    settings = ctx.settings if isinstance(ctx.settings, LintSettings) else LintSettings()
    if not settings.ignore:
        return DoctorResult(id=IGNORE_IDS, ok=True, detail="lint.ignore is empty")
    resolver = Resolver(loaded_rules())
    for each in settings.ignore:
        resolver.resolve(parse_suppression(each, option="lint.ignore").rule)
    if resolver.warnings:
        return DoctorResult(id=IGNORE_IDS, ok=True, warn=True, detail="; ".join(resolver.warnings))
    return DoctorResult(id=IGNORE_IDS, ok=True, detail="every lint.ignore id is installed")
