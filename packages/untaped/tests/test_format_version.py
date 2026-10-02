"""config.yml and state.yml refuse a newer on-disk format, on read and on write."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from test_management.support import write_config
from untaped.config_file import mutate_config, mutate_tool_state, write_config_dict
from untaped.errors import ConfigError
from untaped.settings import FORMAT_VERSION, load_config_yaml
from untaped.state import StateCollection, StateMap


def _config() -> Path:
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_a_file_without_a_stamp_is_format_1(tmp_path: Path) -> None:
    write_config(tmp_path / "c.yml", "active: default\n")
    assert load_config_yaml(tmp_path / "c.yml") == {"active": "default"}


def test_a_newer_format_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "c.yml"
    write_config(path, f"format_version: {FORMAT_VERSION + 1}\n")
    with pytest.raises(ConfigError) as refused:
        load_config_yaml(path)
    assert str(refused.value) == (
        f"{path} was written by a newer untaped (format {FORMAT_VERSION + 1}; "
        f"this release reads format {FORMAT_VERSION}); upgrade untaped"
    )
    assert refused.value.exit_code == 4


@pytest.mark.parametrize("value", ["true", "false", "'2'", "1.5", "0", "-1", "null", "[1]"])
def test_a_malformed_stamp_is_invalid(tmp_path: Path, value: str) -> None:
    path = tmp_path / "c.yml"
    write_config(path, f"format_version: {value}\n")
    with pytest.raises(
        ConfigError, match=r"^invalid format_version in .*: expected a positive integer, got "
    ):
        load_config_yaml(path)


STAMPS = [
    (str(FORMAT_VERSION + 1), "newer untaped"),
    ("'2'", "invalid format_version"),
    ("true", "invalid format_version"),
]


def _replace_config(path: Path) -> None:
    write_config_dict({"active": "other"}, path)


def _mutate_config(path: Path) -> None:
    mutate_config(lambda data: data.update(active="other"), path)


@pytest.mark.parametrize("write", [_replace_config, _mutate_config])
@pytest.mark.parametrize(("stamp", "match"), STAMPS)
def test_writes_never_touch_a_refused_config(
    write: Callable[[Path], None], stamp: str, match: str
) -> None:
    """Newer and invalid stamps alike: no write path overwrites the file."""
    path = _config()
    text = f"format_version: {stamp}\nactive: default\nprofiles: {{}}\n"
    write_config(path, text)
    with pytest.raises(ConfigError, match=match):
        write(path)
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(("stamp", "match"), STAMPS)
def test_state_writes_never_touch_a_refused_state_file(stamp: str, match: str) -> None:
    state = _config().with_name("state.yml")
    text = f"format_version: {stamp}\ndemo:\n  items: []\n"
    write_config(state, text)
    with pytest.raises(ConfigError, match=match):
        StateCollection("demo", "items", id_field="id").upsert({"id": "a"})
    with pytest.raises(ConfigError, match=match):
        StateMap("demo", "aliases").set("web", "org/web")
    with pytest.raises(ConfigError, match=match):
        mutate_tool_state("demo", lambda section: section.update(items=[]))
    assert state.read_text(encoding="utf-8") == text


def test_an_explicit_current_stamp_reads_like_none(tmp_path: Path) -> None:
    body = "active: default\nprofiles:\n  default: {}\n"
    write_config(tmp_path / "with.yml", f"format_version: {FORMAT_VERSION}\n{body}")
    write_config(tmp_path / "without.yml", body)
    with_stamp = load_config_yaml(tmp_path / "with.yml")
    assert with_stamp.pop("format_version") == FORMAT_VERSION
    assert with_stamp == load_config_yaml(tmp_path / "without.yml")


def test_an_explicit_stamp_survives_writes() -> None:
    path = _config()
    stamped = f"format_version: {FORMAT_VERSION}\nactive: default\nprofiles:\n  default: {{}}\n"
    write_config(path, stamped)
    mutate_config(lambda data: data["profiles"].update(other={}), path)
    assert path.read_text(encoding="utf-8").startswith(f"format_version: {FORMAT_VERSION}\n")
    state = path.with_name("state.yml")
    write_config(state, f"format_version: {FORMAT_VERSION}\ndemo:\n  items: []\n")
    collection = StateCollection("demo", "items", id_field="id")
    collection.upsert({"id": "a"})
    assert state.read_text(encoding="utf-8").startswith(f"format_version: {FORMAT_VERSION}\n")
    assert collection.entries() == [{"id": "a"}]
