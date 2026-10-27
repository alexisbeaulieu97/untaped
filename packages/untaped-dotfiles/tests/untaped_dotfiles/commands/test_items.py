"""``dotfiles items``, ``enable`` and ``disable`` end to end."""

from __future__ import annotations

import pytest

from dotfiles_test_support import (
    Upstream,
    as_json,
    run,
    subscribe,
)

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_enable_records_the_policy_and_skips(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    rows = as_json(["enable", "fish", "claude", "--skip", "settings"])
    assert [(r["name"], r["action"], r["policy"]) for r in rows] == [
        ("fish", "created", "manual"),
        ("claude", "created", "sync"),
    ]
    again = as_json(["enable", "fish", "--policy", "sync"])
    assert [(r["name"], r["action"], r["policy"]) for r in again] == [("fish", "updated", "sync")]
    items = {r["name"]: r for r in as_json(["items"])}
    assert (items["fish"]["policy"], items["claude"]["skip"]) == ("sync", ["settings"])
    assert run(["enable", "fish", "--skip", "nope"]).exit_code == 2
    assert run(["enable"]).exit_code == 2
    assert run(["enable", "fish", "--all"]).exit_code == 2
    assert run(["enable", "nope"]).exit_code == 1


def test_subscribe_and_enable_print_the_next_step_as_a_hint(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    subscribed = run(["subscribe", str(bare)])
    assert subscribed.exit_code == 0, subscribed.output
    assert "subscribed 'dotfiles' (4 items)\n" in subscribed.stderr
    assert "hint: run `untaped dotfiles enable ITEMS`" in subscribed.stderr
    enabled = run(["enable", "fish"])
    assert enabled.exit_code == 0, enabled.output
    assert "enabled 1 item\n" in enabled.stderr
    assert "hint: run `untaped dotfiles apply`" in enabled.stderr


def test_enable_all_takes_every_item_that_applies_here(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    rows = as_json(["enable", "--all"])
    assert [r["name"] for r in rows] == ["fish", "starship", "claude"]


def test_an_ambiguous_bare_name_is_refused_with_a_hint(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    other, _ = make_upstream("work")
    subscribe(bare)
    subscribe(other)
    result = run(["enable", "fish"])
    assert result.exit_code == 2, result.output
    assert "several repos" in result.stderr and "--repo" in result.stderr
    assert run(["enable", "fish", "--repo", "work"]).exit_code == 0
    assert run(["status", "fish"]).exit_code == 0  # one enabled: not ambiguous
    run(["enable", "fish", "--repo", "dotfiles"])
    assert run(["status", "fish"]).exit_code == 2
    assert run(["disable", "fish"]).exit_code == 2
    assert [r["repo"] for r in as_json(["disable", "fish", "--repo", "work"])] == ["work"]
