"""A cache's max age is the setting ``Cache`` generates, never a field of a plugin's settings.

Declaring a ``Cache`` on ``PluginSpec.caches`` gives it
``<plugin>.caches.<cache>.max_age_seconds``, documented, typed and named the
same way for every plugin, and the ``--refresh`` help names it. A settings
field whose name says ``max_age``, ``stale``, ``expir`` or ``ttl`` is a
hand-made one and fails here. The exemptions are fields that are not a
cache's max age; :func:`test_the_exemptions_are_still_needed` fails once one
is gone.
"""

from __future__ import annotations

from collections.abc import Iterator
from pkgutil import resolve_name

from pydantic import BaseModel

from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk import Plugin
from untaped.sdk._schema import unwrap_optional

#: Parts of a field name that say a max age; ``ttl`` counts only as a whole word.
WORDS = ("max_age", "stale", "expir")
#: ``(plugin, field path)`` → why it stays.
EXEMPT = {
    ("recipe", "backup_max_age_days"): "a retention policy for backups, not a cache",
}


def hand_made(model: type[BaseModel], prefix: str = "") -> Iterator[str]:
    """Every field path of ``model`` (nested models included) whose name says a max age."""
    for name, info in model.model_fields.items():
        annotation = unwrap_optional(info.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from hand_made(annotation, f"{prefix}{name}.")
        elif any(word in name for word in WORDS) or "ttl" in name.split("_"):
            yield f"{prefix}{name}"


def _settings(candidate: PluginCandidate) -> type[BaseModel] | None:
    """The settings model of an 11.x spec or a 12.0 plugin class."""
    target = resolve_name(str(candidate.target))
    if isinstance(target, PluginSpec):
        return target.settings
    if isinstance(target, type) and issubclass(target, Plugin):
        return target.settings
    return None


def _found(candidates: tuple[PluginCandidate, ...]) -> set[tuple[str, str]]:
    return {
        (candidate.name, path)
        for candidate in candidates
        if (settings := _settings(candidate)) is not None
        for path in hand_made(settings)
    }


def test_no_plugin_declares_a_max_age_setting_by_hand(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    found = _found(first_party_candidates)
    stray = sorted(f"{plugin}.{path}" for plugin, path in found - set(EXEMPT))
    assert not stray, (
        f"{stray}: declare a Cache on the spec (PluginSpec.caches); its setting "
        "<plugin>.caches.<cache>.max_age_seconds is generated, and Cache(renamed_from=) "
        "moves an old key into it; see docs/caches.md"
    )


def test_the_exemptions_are_still_needed(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    gone = sorted(set(EXEMPT) - _found(first_party_candidates))
    assert not gone, f"{gone} no longer exist: drop them from EXEMPT"
