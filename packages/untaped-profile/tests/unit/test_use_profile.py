from __future__ import annotations

from typing import Any

import pytest
from untaped_profile.application import UseProfile

from untaped import ConfigError


def test_persists_active(repo: Any) -> None:
    UseProfile(repo)("stage")
    assert repo.active_name() == "stage"


def test_unknown_profile_raises(repo: Any) -> None:
    with pytest.raises(ConfigError, match="ghost"):
        UseProfile(repo)("ghost")


def test_default_is_always_valid(empty_repo_factory: Any) -> None:
    repo = empty_repo_factory(profiles={"default": {}}, active=None)
    UseProfile(repo)("default")
    assert repo.active_name() == "default"


def test_accepts_active_writer_without_data_write_surface() -> None:
    """Validates the parallel-sibling split: ``UseProfile`` runs against a
    stub that has ``set_active`` but **not** ``write`` / ``delete`` /
    ``rename``. A linear ``ProfileReader ⊂ ProfileWriter ⊂
    ProfileRepository`` chain would force ``UseProfile`` to type-accept
    those data-write methods it never touches; this test pins the
    sibling shape.
    """
    from untaped import ProfileSource

    class ActiveOnly:
        def __init__(self) -> None:
            self._active: str | None = "default"

        def names(self) -> list[str]:
            return ["default", "prod"]

        def active_name(self) -> str | None:
            return self._active

        def persisted_active_name(self) -> str | None:
            return self._active

        def classify_active(self) -> tuple[str | None, ProfileSource]:
            return self._active, "config"

        def read(self, name: str) -> dict[str, Any] | None:
            return {} if name in ("default", "prod") else None

        def resolved(self, name: str) -> dict[str, Any]:
            return {}

        def set_active(self, name: str) -> None:
            self._active = name

    repo = ActiveOnly()
    UseProfile(repo)("prod")
    assert repo.active_name() == "prod"
