"""config.yml and state.yml refuse a newer on-disk format, on read and on write."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from untaped.config_file import mutate_config, mutate_tool_state, write_config_dict
from untaped.errors import ConfigError
from untaped.settings import FORMAT_VERSION, check_state_section_name, load_config_yaml
from untaped.state import StateCollection, StateMap


def _config() -> Path:
    return Path(os.environ["UNTAPED_CONFIG"])


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_file_without_a_stamp_is_format_1(tmp_path: Path) -> None:
    _write(tmp_path / "c.yml", "active: default\n")
    assert load_config_yaml(tmp_path / "c.yml") == {"active": "default"}


def test_the_current_format_is_accepted(tmp_path: Path) -> None:
    _write(tmp_path / "c.yml", f"format_version: {FORMAT_VERSION}\nactive: default\n")
    assert load_config_yaml(tmp_path / "c.yml")["active"] == "default"


def test_a_newer_format_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "c.yml"
    _write(path, f"format_version: {FORMAT_VERSION + 1}\n")
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
    _write(path, f"format_version: {value}\n")
    with pytest.raises(
        ConfigError, match=r"^invalid format_version in .*: expected a positive integer, got "
    ):
        load_config_yaml(path)


def test_writes_never_touch_a_newer_config() -> None:
    path = _config()
    text = f"format_version: {FORMAT_VERSION + 1}\nactive: default\nprofiles: {{}}\n"
    _write(path, text)
    with pytest.raises(ConfigError, match="newer untaped"):
        write_config_dict({"active": "other"}, path)
    with pytest.raises(ConfigError, match="newer untaped"):
        mutate_config(lambda data: data.update(active="other"), path)
    assert path.read_text(encoding="utf-8") == text


def test_state_writes_never_touch_a_newer_state_file() -> None:
    state = _config().with_name("state.yml")
    text = f"format_version: {FORMAT_VERSION + 1}\ndemo:\n  items: []\n"
    _write(state, text)
    with pytest.raises(ConfigError, match="newer untaped"):
        StateCollection("demo", "items", id_field="id").upsert({"id": "a"})
    assert state.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("stamp", [str(FORMAT_VERSION + 1), "'2'", "true"])
def test_writes_never_touch_a_refused_config(stamp: str) -> None:
    """Newer and invalid stamps alike: no write path overwrites the file."""
    path = _config()
    text = f"format_version: {stamp}\nactive: default\nprofiles: {{}}\n"
    _write(path, text)
    for write in (
        lambda: write_config_dict({"active": "other"}, path),
        lambda: mutate_config(lambda data: data.update(active="other"), path),
    ):
        with pytest.raises(ConfigError):
            write()
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("stamp", [str(FORMAT_VERSION + 1), "'2'", "true"])
def test_state_writes_never_touch_a_refused_state_file(stamp: str) -> None:
    state = _config().with_name("state.yml")
    text = f"format_version: {stamp}\ndemo:\n  items: []\n"
    _write(state, text)
    with pytest.raises(ConfigError):
        StateCollection("demo", "items", id_field="id").upsert({"id": "a"})
    with pytest.raises(ConfigError):
        StateMap("demo", "aliases").set("web", "org/web")
    with pytest.raises(ConfigError):
        mutate_tool_state("demo", lambda section: section.update(items=[]))
    assert state.read_text(encoding="utf-8") == text


def test_an_explicit_current_stamp_reads_like_none(tmp_path: Path) -> None:
    body = "active: default\nprofiles:\n  default: {}\n"
    _write(tmp_path / "with.yml", f"format_version: {FORMAT_VERSION}\n{body}")
    _write(tmp_path / "without.yml", body)
    with_stamp = load_config_yaml(tmp_path / "with.yml")
    assert with_stamp.pop("format_version") == FORMAT_VERSION
    assert with_stamp == load_config_yaml(tmp_path / "without.yml")


def test_an_explicit_stamp_survives_writes() -> None:
    path = _config()
    _write(path, f"format_version: {FORMAT_VERSION}\nactive: default\nprofiles:\n  default: {{}}\n")
    mutate_config(lambda data: data["profiles"].update(other={}), path)
    assert path.read_text(encoding="utf-8").startswith(f"format_version: {FORMAT_VERSION}\n")
    state = path.with_name("state.yml")
    _write(state, f"format_version: {FORMAT_VERSION}\ndemo:\n  items: []\n")
    collection = StateCollection("demo", "items", id_field="id")
    collection.upsert({"id": "a"})
    assert state.read_text(encoding="utf-8").startswith(f"format_version: {FORMAT_VERSION}\n")
    assert collection.entries() == [{"id": "a"}]


def test_format_version_is_never_a_state_section() -> None:
    with pytest.raises(ConfigError, match="reserved"):
        check_state_section_name("format_version")
