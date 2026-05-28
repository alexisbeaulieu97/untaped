"""Unit tests for the ``CurrentProfile`` use case.

The use case classifies *where* the active profile name came from
(``env`` / ``config`` / ``fallback``) and validates that explicit
pointers (env var or persisted ``active:`` key) actually name a profile
that exists. Reading ``os.environ`` is the repository's job — the use
case stays pure and depends only on ``ProfileRepository.classify_active``.
"""

from __future__ import annotations

from typing import Any

import pytest
from untaped_profile.application import CurrentProfile

from untaped import ConfigError


def test_returns_persisted_active_when_env_unset(repo: Any) -> None:
    """`active: prod` in config, no env override → ('prod', 'config')."""
    result = CurrentProfile(repo)()
    assert result.name == "prod"
    assert result.source == "config"


def test_falls_back_to_default_when_nothing_set(empty_repo_factory: Any) -> None:
    """No env override, no `active:` key → ('default', 'fallback').

    Holds even when no profiles exist yet — the user hasn't created any
    config and ``default`` is the conceptual placeholder name.
    """
    repo = empty_repo_factory()
    result = CurrentProfile(repo)()
    assert result.name == "default"
    assert result.source == "fallback"


def test_env_var_overrides_config(empty_repo_factory: Any) -> None:
    """Env override wins over the persisted `active:` value."""
    repo = empty_repo_factory(
        profiles={"default": {}, "prod": {}, "stage": {}},
        active="prod",
        effective_active="stage",
    )
    result = CurrentProfile(repo)()
    assert result.name == "stage"
    assert result.source == "env"


def test_env_var_overrides_fallback(empty_repo_factory: Any) -> None:
    """Env override wins even with no persisted `active:` key."""
    repo = empty_repo_factory(
        profiles={"default": {}, "homelab": {}},
        effective_active="homelab",
    )
    result = CurrentProfile(repo)()
    assert result.name == "homelab"
    assert result.source == "env"


def test_errors_when_env_profile_missing(empty_repo_factory: Any) -> None:
    """If UNTAPED_PROFILE names a profile that doesn't exist, the
    resolver and every other command will reject the same config —
    `current` must reject it too rather than print a name no other
    command can use. Otherwise the documented pipe usage
    `untaped --profile $(untaped profile current)` fails downstream
    with a worse error.
    """
    repo = empty_repo_factory(profiles={"default": {}}, effective_active="ghost")
    with pytest.raises(ConfigError, match="ghost"):
        CurrentProfile(repo)()


def test_errors_when_persisted_active_missing(empty_repo_factory: Any) -> None:
    """Same protection for the persisted `active:` key — a typo or a
    stale reference must fail loudly instead of poisoning pipelines."""
    repo = empty_repo_factory(profiles={"default": {}}, active="ghost")
    with pytest.raises(ConfigError, match="ghost"):
        CurrentProfile(repo)()


def test_fallback_returns_default_even_when_default_missing(empty_repo_factory: Any) -> None:
    """Documents the deliberate behaviour for the no-pointer-set case:
    when neither env nor `active:` is set, we still report 'default' as
    the fallback name even if no on-disk default profile exists. The
    schema defaults are in effect — 'default' is the conceptual
    placeholder name for that situation.

    The validation guard only fires for the env / config sources where
    the user (or environment) explicitly named a profile.
    """
    repo = empty_repo_factory(profiles={"prod": {}})
    result = CurrentProfile(repo)()
    assert result.name == "default"
    assert result.source == "fallback"
