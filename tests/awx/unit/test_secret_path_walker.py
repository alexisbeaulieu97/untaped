"""The single secret-path walker behind read, redact, and remove."""

from __future__ import annotations

from typing import Any

from untaped.capabilities.awx.application.secret_paths import (
    path_slots,
    remove_at,
    replace_at,
    values_at,
)

RECORD: dict[str, Any] = {
    "webhook_key": "k",
    "inputs": {"password": "p", "username": "u"},
    "survey_spec": {"spec": [{"default": "d1", "name": "a"}, {"name": "b"}]},
}


def test_values_at_follows_wildcards_and_skips_missing_keys() -> None:
    assert list(values_at(RECORD, "webhook_key")) == ["k"]
    assert sorted(values_at(RECORD, "inputs.*")) == ["p", "u"]
    assert list(values_at(RECORD, "survey_spec.spec.*.default")) == ["d1"]
    assert list(values_at(RECORD, "inputs.missing")) == []
    assert list(values_at({"a": None}, "a.k")) == []


def test_values_at_reads_tuples() -> None:
    assert list(values_at({"items": ({"s": 1}, {"s": 2})}, "items.*.s")) == [1, 2]


def test_replace_at_overwrites_every_matching_slot() -> None:
    record: dict[str, Any] = {"inputs": {"a": 1, "b": 2}, "list": [1, 2]}
    replace_at(record, "inputs.*", "x")
    replace_at(record, "list.*", "y")
    assert record == {"inputs": {"a": "x", "b": "x"}, "list": ["y", "y"]}


def test_remove_at_deletes_keys_and_whole_lists() -> None:
    record: dict[str, Any] = {"inputs": {"a": 1, "b": 2}, "list": [1, 2, 3], "keep": 1}
    remove_at(record, "inputs.a")
    remove_at(record, "list.*")
    assert record == {"inputs": {"b": 2}, "list": [], "keep": 1}


def test_path_slots_ignores_non_containers() -> None:
    assert list(path_slots("text", "*")) == []
    assert list(path_slots([1, 2], "name")) == []
