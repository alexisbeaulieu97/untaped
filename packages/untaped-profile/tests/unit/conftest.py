"""Shared test scaffolding for the profile use cases.

The fake ``ProfileRepository`` is the only thing application-layer tests
need — the use cases never know about YAML, files, or the resolver.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest


class FakeProfileRepository:
    """In-memory ``ProfileRepository`` that satisfies the application Protocol."""

    def __init__(
        self,
        profiles: dict[str, dict[str, Any]] | None = None,
        active: str | None = None,
        effective_active: str | None = None,
    ) -> None:
        self._profiles: dict[str, dict[str, Any]] = copy.deepcopy(profiles or {})
        self._persisted_active = active
        # Simulate a sticky env override (e.g. UNTAPED_PROFILE) — set_active
        # updates the persisted pointer but the override keeps winning, just
        # like the real ProfileFileRepository.
        self._effective_override = effective_active

    # ----- queries -----

    def names(self) -> list[str]:
        return list(self._profiles.keys())

    def active_name(self) -> str | None:
        return self._effective_override or self._persisted_active

    def persisted_active_name(self) -> str | None:
        return self._persisted_active

    def read(self, name: str) -> dict[str, Any] | None:
        profile = self._profiles.get(name)
        return copy.deepcopy(profile) if profile is not None else None

    def resolved(self, name: str) -> dict[str, Any]:
        """Merge default ⤥ named, returning the effective dict."""
        merged: dict[str, Any] = {}
        for src_name in ("default", name):
            src = self._profiles.get(src_name)
            if not src:
                continue
            _deep_merge_into(merged, src)
        return merged

    # ----- mutations -----

    def write(self, name: str, data: dict[str, Any]) -> None:
        self._profiles[name] = copy.deepcopy(data)

    def delete(self, name: str) -> bool:
        return self._profiles.pop(name, None) is not None

    def rename(self, old: str, new: str) -> None:
        if old not in self._profiles:
            raise KeyError(old)
        if new in self._profiles:
            raise ValueError(new)
        self._profiles[new] = self._profiles.pop(old)
        if self._persisted_active == old:
            self._persisted_active = new

    def set_active(self, name: str) -> None:
        self._persisted_active = name


def _deep_merge_into(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict):
            existing = dst.get(key)
            child = existing if isinstance(existing, dict) else {}
            dst[key] = child
            _deep_merge_into(child, value)
        else:
            dst[key] = value


@pytest.fixture
def repo() -> FakeProfileRepository:
    return FakeProfileRepository(
        profiles={
            "default": {"log_level": "INFO", "awx": {"api_prefix": "/api/v2/"}},
            "prod": {"awx": {"base_url": "https://prod"}},
            "stage": {"awx": {"base_url": "https://stage"}},
        },
        active="prod",
    )


@pytest.fixture
def empty_repo_factory():  # type: ignore[no-untyped-def]
    """Return a callable that builds a fresh ``FakeProfileRepository`` with
    custom seed data — used by tests that need a different fixture shape."""

    def _make(
        profiles: dict[str, dict[str, Any]] | None = None,
        active: str | None = None,
        effective_active: str | None = None,
    ) -> FakeProfileRepository:
        return FakeProfileRepository(
            profiles=profiles, active=active, effective_active=effective_active
        )

    return _make
