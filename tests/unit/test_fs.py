"""Behavioral tests for filesystem input helpers."""

from pathlib import Path

import pytest


def test_read_structured_file_yaml(tmp_path: Path) -> None:
    from untaped.fs import read_structured_file

    f = tmp_path / "payload.yml"
    f.write_text("fields:\n  summary: hi\n", encoding="utf-8")
    assert read_structured_file(f) == {"fields": {"summary": "hi"}}


def test_read_structured_file_json_by_suffix(tmp_path: Path) -> None:
    from untaped.fs import read_structured_file

    f = tmp_path / "payload.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    assert read_structured_file(f) == {"a": 1}


def test_read_structured_file_empty_yaml_is_empty_dict(tmp_path: Path) -> None:
    from untaped.fs import read_structured_file

    f = tmp_path / "empty.yml"
    f.write_text("", encoding="utf-8")
    assert read_structured_file(f) == {}


def test_read_structured_file_rejects_non_object(tmp_path: Path) -> None:
    from untaped.errors import ConfigError
    from untaped.fs import read_structured_file

    f = tmp_path / "list.yml"
    f.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must contain an object"):
        read_structured_file(f)


def test_read_structured_file_missing_file_is_config_error(tmp_path: Path) -> None:
    from untaped.errors import ConfigError
    from untaped.fs import read_structured_file

    with pytest.raises(ConfigError, match="could not read"):
        read_structured_file(tmp_path / "absent.yml")
