"""Manifest parsing and every rule it enforces."""

from __future__ import annotations

import pytest

from untaped_dotfiles.domain.manifest import parse_manifest
from untaped_dotfiles.errors import ManifestError

GOOD = """\
version: 1
items:
  fish:
    policy: sync
    os: [linux]
    files:
      - source: fish/config.fish
        target: ~/.config/fish/config.fish
      - name: settings
        source: claude/settings.json
        target: ~/.claude/settings.json
        mode: merge
        unless: [dev]
"""


def test_a_manifest_parses_with_defaults() -> None:
    manifest = parse_manifest(GOOD, where="repo:dotfiles.yml")
    item = manifest.items["fish"]
    assert (item.policy, item.os, item.description) == ("sync", ("linux",), "")
    first, second = item.files
    assert (first.mode, first.key, first.merge_format) == ("link", "fish/config.fish", None)
    assert (second.mode, second.key, second.merge_format) == ("merge", "settings", "json")


def test_an_empty_manifest_has_no_items() -> None:
    assert parse_manifest("", where="r:m").items == {}
    assert parse_manifest("items: {}\n", where="r:m").items == {}


def test_policy_defaults_to_manual() -> None:
    text = "items:\n  a:\n    files:\n      - {source: a, target: ~/a}\n"
    assert parse_manifest(text, where="r:m").items["a"].policy == "manual"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("version: 2\nitems: {}\n", "version 2 is not supported"),
        ("- a\n", "must contain a mapping"),
        ("items: [\n", "invalid YAML"),
        ("items:\n  Fish:\n    files: [{source: a, target: ~/a}]\n", "item name 'Fish'"),
        ("items:\n  a:\n    files: []\n", "files"),
        ("items:\n  a:\n    files: [{source: /etc/a, target: ~/a}]\n", "relative to the repo"),
        ("items:\n  a:\n    files: [{source: ../a, target: ~/a}]\n", "'.' or '..'"),
        ("items:\n  a:\n    files: [{source: a, target: a}]\n", "must start with '~/' or '/'"),
        ("items:\n  a:\n    files: [{source: a, target: ~/../a}]\n", "'..'"),
        ("items:\n  a:\n    files: [{source: a, target: ~/a, mode: merge}]\n", "extension"),
        ("items:\n  a:\n    files: [{source: a, target: ~/a, format: json}]\n", "merge files only"),
        ("items:\n  a:\n    files: [{source: a, target: ~/a, mode: move}]\n", "mode"),
        (
            "items:\n  a:\n    files: [{source: a, target: ~/a}, {source: b, target: ~/a}]\n",
            "listed twice",
        ),
        ("items:\n  a:\n    files: [{source: a, target: ~/a, extra: 1}]\n", "extra"),
        ("items:\n  a:\n    policy: never\n    files: [{source: a, target: ~/a}]\n", "policy"),
        ("items:\n  a:\n    os: [beos]\n    files: [{source: a, target: ~/a}]\n", "os"),
    ],
)
def test_manifest_rules(text: str, fragment: str) -> None:
    with pytest.raises(ManifestError) as caught:
        parse_manifest(text, where="repo:dotfiles.yml")
    assert "repo:dotfiles.yml" in str(caught.value)
    assert fragment in str(caught.value)
    assert caught.value.category == "invalid"


def test_two_entries_may_share_a_source_under_different_names() -> None:
    text = (
        "items:\n  a:\n    files:\n"
        "      - {source: skills, target: ~/.agents/skills/mine}\n"
        "      - {name: harness, source: skills, target: ~/.claude/skills/mine}\n"
    )
    item = parse_manifest(text, where="r:m").items["a"]
    assert [entry.key for entry in item.files] == ["skills", "harness"]


def test_merge_format_follows_the_target_extension_or_the_format_key() -> None:
    text = (
        "items:\n  a:\n    files:\n"
        "      - {source: a, target: ~/a.yaml, mode: merge}\n"
        "      - {source: b, target: ~/b.conf, mode: merge, format: yaml}\n"
    )
    item = parse_manifest(text, where="r:m").items["a"]
    assert [entry.merge_format for entry in item.files] == ["yaml", "yaml"]
