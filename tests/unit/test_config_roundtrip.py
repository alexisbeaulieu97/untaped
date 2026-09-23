"""Config writes preserve the user's comments, key order and formatting.

``mutate_config`` rewrites only the keys a mutation changed; everything else
in ``config.yml`` (comments, key order, quoting, flow style) stays as the user
wrote it, and values it writes read back with the same types.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from untaped.config_file import (
    mutate_config,
    mutate_tool_state,
    read_config_dict,
    set_at_path,
    unset_at_path,
    write_config_dict,
)
from untaped.profile.repository import ProfileFileRepository

COMMENTED = """\
# untaped config -- hand edited
active: work   # the profile I use most

profiles:
  # shared base layer
  default:
    http:
      verify_ssl: yes     # YAML 1.1 boolean, keep as written
      timeout: 30
    github:
      base_url: 'https://api.github.com'
  work:
    github:
      token: old-token  # rotate monthly
    ui: {theme: classic}

# capability state below
workspace:
  workspaces:
    - name: alpha   # first
      path: /tmp/alpha
"""


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(path))
    path.write_text(COMMENTED, encoding="utf-8")
    return path


def _replace_line(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


def test_set_preserves_comments_order_and_untouched_formatting(cfg: Path) -> None:
    mutate_config(
        lambda data: set_at_path(data, ("profiles", "work", "github", "token"), "new-token")
    )

    expected = _replace_line(
        COMMENTED, "token: old-token  # rotate monthly", "token: new-token  # rotate monthly"
    )
    assert cfg.read_text(encoding="utf-8") == expected


def test_new_keys_are_appended_without_reordering(cfg: Path) -> None:
    mutate_config(lambda data: set_at_path(data, ("profiles", "work", "log_level"), "DEBUG"))

    text = cfg.read_text(encoding="utf-8")
    assert text.startswith("# untaped config -- hand edited\nactive: work   # the profile")
    assert "      token: old-token  # rotate monthly\n" in text
    assert "    log_level: DEBUG\n" in text
    assert read_config_dict(cfg)["profiles"]["work"]["log_level"] == "DEBUG"
    # Keys keep their original (unsorted) order.
    assert list(read_config_dict(cfg)) == ["active", "profiles", "workspace"]


def test_unset_keeps_surrounding_comments(cfg: Path) -> None:
    mutate_config(lambda data: unset_at_path(data, ("profiles", "default", "http", "timeout")))

    text = cfg.read_text(encoding="utf-8")
    assert "timeout" not in text
    assert "verify_ssl: yes     # YAML 1.1 boolean, keep as written" in text
    assert "# shared base layer" in text
    assert "# capability state below" in text


def test_untouched_yaml_1_1_scalars_keep_their_meaning(cfg: Path) -> None:
    mutate_config(lambda data: set_at_path(data, ("profiles", "work", "log_level"), "DEBUG"))

    assert read_config_dict(cfg)["profiles"]["default"]["http"]["verify_ssl"] is True


@pytest.mark.parametrize(
    "value", ["no", "on", "0123456", "1:30", "null", "~", "true", "1e3", "", "p4ss #word"]
)
def test_written_strings_read_back_as_strings(cfg: Path, value: str) -> None:
    mutate_config(lambda data: set_at_path(data, ("profiles", "work", "github", "token"), value))

    assert read_config_dict(cfg)["profiles"]["work"]["github"]["token"] == value
    text = cfg.read_text(encoding="utf-8")
    assert yaml.safe_load(text)["active"] == "work"
    assert "# rotate monthly" in text  # round-tripped, not re-dumped


def test_tool_state_write_preserves_comments(cfg: Path) -> None:
    def _add(state: dict[str, object]) -> None:
        rows = state["workspaces"]
        assert isinstance(rows, list)
        rows.append({"name": "beta", "path": "/tmp/beta"})

    mutate_tool_state("workspace", _add)

    text = cfg.read_text(encoding="utf-8")
    assert "    - name: alpha   # first\n" in text
    assert "# untaped config -- hand edited" in text
    assert "active: work   # the profile I use most" in text
    assert [row["name"] for row in read_config_dict(cfg)["workspace"]["workspaces"]] == [
        "alpha",
        "beta",
    ]


def test_profile_writes_preserve_comments(cfg: Path) -> None:
    repo = ProfileFileRepository()
    repo.set_active("default")
    repo.write("staging", {})

    text = cfg.read_text(encoding="utf-8")
    assert text.startswith("# untaped config -- hand edited\nactive: default")
    assert "# the profile I use most" in text
    assert "      token: old-token  # rotate monthly\n" in text
    assert read_config_dict(cfg)["profiles"]["staging"] == {}


def test_profile_rename_keeps_the_profile_in_place_with_its_comments(cfg: Path) -> None:
    ProfileFileRepository().rename("work", "prod")

    expected = _replace_line(COMMENTED, "active: work ", "active: prod ")
    expected = _replace_line(expected, "  work:\n", "  prod:\n")
    assert cfg.read_text(encoding="utf-8") == expected


def test_fresh_file_and_full_replacement_still_write(tmp_path: Path) -> None:
    path = tmp_path / "fresh.yml"
    write_config_dict({"b": {"y": 1, "x": [1, 2]}, "a": "no"}, path)

    assert read_config_dict(path) == {"b": {"y": 1, "x": [1, 2]}, "a": "no"}


def test_unparseable_original_falls_back_to_a_plain_dump(tmp_path: Path) -> None:
    path = tmp_path / "dupes.yml"
    # PyYAML accepts duplicate keys (last wins); ruamel.yaml rejects them.
    path.write_text("a: 1\na: 2  # dup\n", encoding="utf-8")

    mutate_config(lambda data: data.__setitem__("b", "no"), path)

    assert read_config_dict(path) == {"a": 2, "b": "no"}


def test_keys_parsed_differently_are_rewritten_consistently(tmp_path: Path) -> None:
    path = tmp_path / "keys.yml"
    path.write_text("# c\nflags:\n  on: 1  # yaml 1.1 bool key\n", encoding="utf-8")

    mutate_config(lambda data: data["flags"].__setitem__("x", 2), path)

    assert read_config_dict(path) == {"flags": {True: 1, "x": 2}}
