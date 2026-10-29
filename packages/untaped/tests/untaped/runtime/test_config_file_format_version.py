"""config.yml reads only format 2 and refuses a newer format.

An 11.x config.yml (unstamped, or stamped 1) is refused on read and on write,
and so is a home holding 11.x's top-level state.yml: 12.0 has no upgrade path.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from core_test_support.commands.support import write_config
from untaped.runtime.config_file import (
    mutate_config,
    write_config_dict,
)
from untaped.runtime.settings import (
    FORMAT_VERSION,
    get_settings,
    load_config_yaml,
)
from untaped.sdk.errors import ConfigError


@pytest.mark.parametrize("text", ["", "# nothing yet\n", "{}\n"])
def test_an_empty_config_is_a_fresh_home(tmp_path: Path, text: str) -> None:
    write_config(tmp_path / "c.yml", text)
    assert load_config_yaml(tmp_path / "c.yml") == {}


@pytest.mark.parametrize(
    "text", ["profiles:\n  default: {}\n", "awx: {}\n", "format_version: 1\nawx: {}\n"]
)
def test_an_11x_config_is_refused_naming_the_way_out(tmp_path: Path, text: str) -> None:
    path = tmp_path / "c.yml"
    write_config(path, text)
    with pytest.raises(ConfigError) as refused:
        load_config_yaml(path)
    assert str(refused.value) == (
        f"{path} was written by untaped 11.x (format 1); this release reads format "
        f"{FORMAT_VERSION} and has no upgrade path"
    )
    assert refused.value.hint == (
        "delete it and run `untaped setup`, or keep untaped 11.1 for this home; "
        f"a file written for 12.0 starts with `format_version: {FORMAT_VERSION}`"
    )
    assert refused.value.exit_code == 4


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("active: prod\n", r"`active:` in .* is not read"),
        ("profiles:\n  default: {}\n", r"profiles.default in .* is not read"),
    ],
)
def test_11x_keys_in_a_format_2_config_are_refused(tmp_path: Path, text: str, match: str) -> None:
    path = tmp_path / "c.yml"
    write_config(path, f"format_version: {FORMAT_VERSION}\n{text}")
    with pytest.raises(ConfigError, match=match) as refused:
        load_config_yaml(path)
    assert refused.value.exit_code == 4


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
    ("1", "untaped 11.x"),
    ("'2'", "invalid format_version"),
    ("true", "invalid format_version"),
]


def _replace_config(path: Path) -> None:
    write_config_dict({"format_version": FORMAT_VERSION, "other": {}}, path)


def _mutate_config(path: Path) -> None:
    mutate_config(lambda data: data.update(other={}), path)


@pytest.mark.parametrize("write", [_replace_config, _mutate_config])
@pytest.mark.parametrize(("stamp", "match"), [*STAMPS, (None, "untaped 11.x")])
def test_writes_never_touch_a_refused_config(
    _isolated_config: Path, write: Callable[[Path], None], stamp: str | None, match: str
) -> None:
    """Newer, 11.x and invalid stamps alike: no write path overwrites the file."""
    path = _isolated_config
    text = ("" if stamp is None else f"format_version: {stamp}\n") + "demo: {}\n"
    write_config(path, text)
    with pytest.raises(ConfigError, match=match):
        write(path)
    assert path.read_text(encoding="utf-8") == text


def _refused(read: Callable[[], object], match: str) -> None:
    with pytest.raises(ConfigError, match=match) as refused:
        read()
    assert refused.value.exit_code == 4


@pytest.mark.parametrize(("stamp", "match"), STAMPS)
def test_settings_refuse_a_stamped_config(_isolated_config: Path, stamp: str, match: str) -> None:
    write_config(_isolated_config, f"format_version: {stamp}\ndemo: {{}}\n")
    _refused(get_settings, match)


def test_a_home_holding_11x_shared_state_is_refused(_isolated_config: Path) -> None:
    write_config(_isolated_config, f"format_version: {FORMAT_VERSION}\n")
    state = _isolated_config.with_name("state.yml")
    write_config(state, "demo:\n  items: []\n")
    with pytest.raises(ConfigError, match=r"written by untaped 11\.x") as refused:
        get_settings()
    assert refused.value.exit_code == 4
    assert refused.value.hint == "delete it, or keep untaped 11.1 for this home"
    assert state.read_text(encoding="utf-8") == "demo:\n  items: []\n"


def test_stamps_survive_writes(_isolated_config: Path) -> None:
    path = _isolated_config
    write_config(path, f"format_version: {FORMAT_VERSION}\nprofiles:\n  work: {{}}\n")
    mutate_config(lambda data: data["profiles"].update(other={}), path)
    assert path.read_text(encoding="utf-8").startswith(f"format_version: {FORMAT_VERSION}\n")
