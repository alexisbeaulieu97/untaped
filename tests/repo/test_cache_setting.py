"""A cache's max age is the setting ``Cache`` generates, never a field of a plugin's settings.

Declaring a ``Cache`` on ``PluginSpec.caches`` gives it
``<plugin>.caches.<cache>.max_age_seconds``, documented, typed and named the
same way for every plugin, and the ``--refresh`` help names it. A settings
field whose name says ``max_age``, ``stale``, ``expir`` or ``ttl`` is a
hand-made one and fails here. The exemptions are fields that are not a
cache's max age, or not on a ``Cache`` yet (each names the PR that moves it);
:func:`test_the_exemptions_are_still_needed` fails once one is gone.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, Field

from untaped.config_schema import unwrap_optional
from untaped.plugins.registry import PluginSpec

#: Parts of a field name that say a max age; ``ttl`` counts only as a whole word.
WORDS = ("max_age", "stale", "expir")
#: ``(plugin, field path)`` → why it stays.
EXEMPT = {
    ("recipe", "backup_max_age_days"): "a retention policy for backups, not a cache",
    ("github", "sweep.max_age_seconds"): "until D (#582)",
}


def hand_made(model: type[BaseModel], prefix: str = "") -> Iterator[str]:
    """Every field path of ``model`` (nested models included) whose name says a max age."""
    for name, info in model.model_fields.items():
        annotation = unwrap_optional(info.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from hand_made(annotation, f"{prefix}{name}.")
        elif any(word in name for word in WORDS) or "ttl" in name.split("_"):
            yield f"{prefix}{name}"


def _found(specs: tuple[PluginSpec, ...]) -> set[tuple[str, str]]:
    return {
        (spec.name, path)
        for spec in specs
        if spec.settings is not None
        for path in hand_made(spec.settings)
    }


def test_no_plugin_declares_a_max_age_setting_by_hand(
    first_party_specs: tuple[PluginSpec, ...],
) -> None:
    stray = sorted(f"{plugin}.{path}" for plugin, path in _found(first_party_specs) - set(EXEMPT))
    assert not stray, (
        f"{stray}: declare a Cache on the spec (PluginSpec.caches); its setting "
        "<plugin>.caches.<cache>.max_age_seconds is generated, and Cache(renamed_from=) "
        "moves an old key into it; see docs/caches.md"
    )


def test_the_exemptions_are_still_needed(first_party_specs: tuple[PluginSpec, ...]) -> None:
    gone = sorted(set(EXEMPT) - _found(first_party_specs))
    assert not gone, f"{gone} no longer exist: drop them from EXEMPT"


class _Window(BaseModel):
    max_age_minutes: int = 5
    parallel: int = 4


class _Bad(BaseModel):
    stale_after_seconds: int = 60
    cache_ttl_seconds: int = 60
    window: _Window | None = Field(default_factory=_Window)


class _Good(BaseModel):
    timeout_seconds: float = 30.0
    throttle_seconds: float = 1.0
    window_parallel: int = 4


def test_the_finder_flags_max_age_names_only() -> None:
    assert list(hand_made(_Bad)) == [
        "stale_after_seconds",
        "cache_ttl_seconds",
        "window.max_age_minutes",
    ]
    assert list(hand_made(_Good)) == []
