"""The field vocabulary: positionals, marks, value types, core-filled blocks and ``key``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, get_args

import pytest
from pydantic import BaseModel, Field, ValidationError

from untaped.sdk import (
    AllowHyphen,
    Column,
    ConfigError,
    ExistingFile,
    File,
    Key,
    KeyJson,
    KeyValue,
    Output,
    Parallel,
    Positional,
    Short,
    Stdin,
    UntapedError,
    UsageError,
    key,
)
from untaped.sdk._block import POSITIONAL
from untaped.sdk.fields import CORE_SHORTS, SettingKey, column_fields, key_field


class Suite(BaseModel):
    """A suite."""

    name: str
    cases: list[str] = Field(default_factory=list)


class Server(BaseModel):
    url: str = "https://example.com"


class AcmeSettings(BaseModel):
    greeting: str = "hi"
    server: Server = Field(default_factory=Server)
    backup: Server | None = None


def test_positional_marks_the_field_and_keeps_further_marks() -> None:
    assert get_args(Positional[list[str]]) == (list[str], POSITIONAL)
    assert get_args(Positional[str, AllowHyphen]) == (str, POSITIONAL, AllowHyphen)


def test_short_takes_one_letter() -> None:
    assert Short("-r").flag == "-r"
    with pytest.raises(ValueError, match="one letter"):
        Short("--repo")
    with pytest.raises(ValueError, match="one letter"):
        Short("-1")


def test_core_shorts_are_the_blocks_letters_and_help() -> None:
    assert {"-f", "-c", "-y", "-j", "-h"} == CORE_SHORTS


class Run(BaseModel):
    paths: list[File[Suite]] = Field(default_factory=list)
    out: File[Suite] | None = None
    compare: ExistingFile | None = None
    labels: list[KeyValue] = Field(default_factory=list)
    values: list[KeyJson] = Field(default_factory=list)


def test_a_file_value_is_built_from_a_path_and_loads_its_model(tmp_path: Path) -> None:
    yaml_file = tmp_path / "suite.yml"
    yaml_file.write_text("name: deploy\ncases: [web]\n", encoding="utf-8")
    json_file = tmp_path / "suite.json"
    json_file.write_text(json.dumps({"name": "db"}), encoding="utf-8")
    run = Run(paths=[str(yaml_file), json_file])
    assert [f.path for f in run.paths] == [yaml_file, json_file]
    assert run.paths[0].load() == Suite(name="deploy", cases=["web"])
    assert run.paths[1].load() == Suite(name="db")
    assert run.model_dump(mode="json")["paths"] == [str(yaml_file), str(json_file)]


def test_a_file_that_does_not_validate_names_itself(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("cases: [web]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"bad\.yml"):
        File[Suite](bad).load()


def test_a_file_built_through_its_alias_knows_its_model(tmp_path: Path) -> None:
    target = File[Suite](tmp_path / "out.yml")
    assert target.model is Suite
    with pytest.raises(TypeError, match="no model"):
        _ = File(tmp_path / "x.yml").model


def test_save_writes_yaml_or_json_and_never_replaces_unasked(tmp_path: Path) -> None:
    out = File[Suite](tmp_path / "out.yml")
    out.save(Suite(name="deploy"))
    assert out.load() == Suite(name="deploy")
    with pytest.raises(UntapedError, match="already exists") as raised:
        out.save(Suite(name="other"))
    assert raised.value.exit_code == 1
    out.save(Suite(name="other"), replace=True)
    assert out.load().name == "other"
    as_json = File[Suite](tmp_path / "out.json")
    as_json.save(Suite(name="db", cases=["a"]))
    assert json.loads(as_json.path.read_text(encoding="utf-8")) == {"name": "db", "cases": ["a"]}


def test_existing_file_must_exist(tmp_path: Path) -> None:
    present = tmp_path / "a.json"
    present.write_text("{}", encoding="utf-8")
    assert Run(compare=present).compare == present
    with pytest.raises(ValidationError):
        Run(compare=tmp_path / "missing.json")


def test_key_value_splits_on_the_first_equals() -> None:
    run = Run(labels=["a=b=c", "empty="])
    assert run.labels == [("a", "b=c"), ("empty", "")]
    with pytest.raises(ValidationError, match="KEY=VALUE"):
        Run(labels=["novalue"])
    with pytest.raises(ValidationError, match="KEY=VALUE"):
        Run(labels=["=x"])


def test_key_json_parses_the_value() -> None:
    run = Run(values=['tags=["a"]', "count=3"])
    assert run.values == [("tags", ["a"]), ("count", 3)]
    with pytest.raises(ValidationError, match="not JSON"):
        Run(values=["count=three"])


def test_key_follows_fields_and_nested_models() -> None:
    greeting = key(AcmeSettings).greeting
    assert str(greeting) == "greeting"
    assert repr(greeting) == "key(AcmeSettings).greeting"
    assert str(key(AcmeSettings).server.url) == "server.url"
    assert str(key(AcmeSettings).backup.url) == "backup.url"
    assert SettingKey.model_of(greeting) is AcmeSettings
    assert SettingKey.path_of(key(AcmeSettings).server.url) == ("server", "url")
    assert key(AcmeSettings).greeting == greeting
    assert len({greeting, key(AcmeSettings).greeting}) == 1


def test_a_key_typo_fails_where_it_is_written() -> None:
    with pytest.raises(AttributeError, match="AcmeSettings has no field 'gretting'"):
        _ = key(AcmeSettings).gretting
    with pytest.raises(AttributeError, match="is a value, not a model"):
        _ = key(AcmeSettings).greeting.length
    with pytest.raises(TypeError, match="settings model class"):
        key("acme")  # type: ignore[arg-type]


class Hint(BaseModel):
    setting: SettingKey


def test_a_key_is_a_field_type_dumped_as_its_path() -> None:
    hint = Hint(setting=key(AcmeSettings).server.url)
    assert hint.model_dump(mode="json") == {"setting": "server.url"}


def test_output_lists_its_formats_with_the_plugins_renderers() -> None:
    assert Output().formats == ("table", "json", "yaml", "pipe", "raw")
    assert Output(render={"tree": print}).formats[-1] == "tree"
    assert Output(columns=["key"]).declared_columns == ("key",)


def test_parallel_checks_its_numbers() -> None:
    assert Parallel(cap=10).cap == 10
    assert Parallel(default=key(AcmeSettings).greeting).default == key(AcmeSettings).greeting
    with pytest.raises(ValueError, match="at least 1"):
        Parallel(default=0)
    with pytest.raises(ValueError, match="at least 1"):
        Parallel(cap=0)


def test_annotated_marks_reach_the_field() -> None:
    class Opts(BaseModel):
        repo: Annotated[str, Short("-r")] = Field("x", description="The repo.")

    assert [type(m) for m in Opts.model_fields["repo"].metadata] == [Short]


class Row(BaseModel):
    name: Annotated[str, Key, Column]
    size: int = 0


def test_an_unfilled_output_is_a_table_with_its_declared_columns() -> None:
    output: Output[Row] = Output(columns=("name",))
    assert (output.format, list(output.columns), output.requested) == ("table", ["name"], None)
    output.columns.add("size", "name")
    assert list(output.columns) == ["name", "size"]


def test_a_filled_output_is_a_copy_and_falls_back_to_the_column_marks() -> None:
    default: Output[Row] = Output()
    filled = default._filled(fmt="json", requested=["+size"], columns=column_fields(Row))
    filled.columns.add("size")
    filled.empty("none")
    assert (filled.format, list(filled.columns), filled.requested) == (
        "json",
        ["name", "size"],
        ["+size"],
    )
    assert (default.format, list(default.columns), default.empty_text) == ("table", [], None)


def test_an_unfilled_stdin_gives_back_what_was_given() -> None:
    stdin: Stdin[Row] = Stdin()
    assert stdin.identifiers(["ada"]) == ["ada"]
    assert stdin.values(["ada"]) == ["ada"]
    assert stdin.identifiers([], allow_empty=True) == []
    with pytest.raises(UsageError, match="at least one identifier"):
        stdin.identifiers([])


def test_the_key_and_column_marks_name_their_fields() -> None:
    assert key_field(Row) == "name"
    assert column_fields(Row) == ("name",)
    assert key_field(Suite) is None
