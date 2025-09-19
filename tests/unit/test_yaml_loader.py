from __future__ import annotations

from pathlib import Path

import pytest

from untaped_core.errors import YamlLoadError
from untaped_core.yaml_loader import load_variables_file, load_yaml_file, load_yaml_string


def test_load_yaml_file_reads_mapping(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text("foo: bar\n", encoding="utf-8")

    data = load_yaml_file(config)

    assert data == {"foo": "bar"}


def test_load_yaml_string_handles_invalid_yaml() -> None:
    with pytest.raises(YamlLoadError):
        load_yaml_string("foo: [unclosed", source="inline")


def test_load_variables_file_requires_mapping(tmp_path: Path) -> None:
    variables = tmp_path / "vars.yml"
    variables.write_text("- item\n", encoding="utf-8")

    with pytest.raises(YamlLoadError):
        load_variables_file(variables)
