"""``FakeProfileRepository`` is provided by the ``repo`` fixture in
``tests/unit/conftest.py``; we use ``Any`` for type annotations to dodge
the importlib-mode cross-file import problem.
"""

from __future__ import annotations

from typing import Any

from untaped_profile.application import ListProfiles


def test_lists_every_profile(repo: Any) -> None:
    profiles = ListProfiles(repo)()
    names = [p.name for p in profiles]
    assert sorted(names) == ["default", "prod", "stage"]


def test_marks_active(repo: Any) -> None:
    profiles = {p.name: p for p in ListProfiles(repo)()}
    assert profiles["prod"].is_active is True
    assert profiles["default"].is_active is False
    assert profiles["stage"].is_active is False


def test_active_falls_back_to_default_when_unset(empty_repo_factory: Any) -> None:
    repo = empty_repo_factory(profiles={"default": {}, "stage": {}}, active=None)
    profiles = {p.name: p for p in ListProfiles(repo)()}
    assert profiles["default"].is_active is True
    assert profiles["stage"].is_active is False


def test_key_count_reflects_leaf_count(repo: Any) -> None:
    profiles = {p.name: p for p in ListProfiles(repo)()}
    # default sets log_level + awx.api_prefix = 2 leaves
    assert profiles["default"].key_count == 2
    # prod sets only awx.base_url = 1 leaf
    assert profiles["prod"].key_count == 1


def test_empty_repo_returns_empty_list(empty_repo_factory: Any) -> None:
    repo = empty_repo_factory(profiles={})
    assert ListProfiles(repo)() == []
