"""Unit tests for the ``CurrentProfile`` use case.

The use case classifies *where* the active profile name came from so the
CLI can show that on stderr without losing pipe-friendliness on stdout.
The three sources, in precedence order: env var ``UNTAPED_PROFILE``,
the ``active:`` key persisted in the config file, and the implicit
``default`` fallback.
"""

from __future__ import annotations

from typing import Any

import pytest
from untaped_profile.application import CurrentProfile


def test_returns_persisted_active_when_env_unset(
    repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`active: prod` in config, no env var → ('prod', 'config')."""
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    result = CurrentProfile(repo)()
    assert result.name == "prod"
    assert result.source == "config"


def test_falls_back_to_default_when_nothing_set(
    empty_repo_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env var, no `active:` key → ('default', 'fallback').

    Holds even when no profiles exist yet — the user hasn't created any
    config and ``default`` is the resolver's implicit choice.
    """
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    repo = empty_repo_factory()
    result = CurrentProfile(repo)()
    assert result.name == "default"
    assert result.source == "fallback"


def test_env_var_overrides_config(repo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """UNTAPED_PROFILE wins over the persisted `active:` value."""
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    result = CurrentProfile(repo)()
    assert result.name == "stage"
    assert result.source == "env"


def test_env_var_overrides_fallback(
    empty_repo_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNTAPED_PROFILE wins even with an empty config (no `active:` key)."""
    monkeypatch.setenv("UNTAPED_PROFILE", "homelab")
    repo = empty_repo_factory()
    result = CurrentProfile(repo)()
    assert result.name == "homelab"
    assert result.source == "env"


def test_empty_env_var_treated_as_unset(repo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty UNTAPED_PROFILE must not register as a real override —
    matches ``effective_active_profile_name`` which only treats non-empty
    strings as a valid override."""
    monkeypatch.setenv("UNTAPED_PROFILE", "")
    result = CurrentProfile(repo)()
    assert result.name == "prod"
    assert result.source == "config"
