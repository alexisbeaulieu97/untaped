"""The field vocabulary: positionals, marks, value types, core-filled blocks and ``key``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, get_args

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from untaped.sdk import (
    AllowHyphen,
    Column,
    ConfigError,
    Confirm,
    ExistingFile,
    File,
    Following,
    Key,
    KeyJson,
    KeyValue,
    Limit,
    NeedsYesError,
    OutcomeRecord,
    Output,
    Parallel,
    Positional,
    Record,
    Refresh,
    Short,
    Stdin,
    UntapedError,
    UsageError,
    key,
)
from untaped.sdk._block import POSITIONAL, Invocation
from untaped.sdk.fields import CORE_SHORTS, SettingKey, column_fields, default_columns, key_field


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
    run = Run(labels=["a=b=c", "empty=", " spaced = kept "])
    assert run.labels == [("a", "b=c"), ("empty", ""), ("spaced", " kept ")]
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
    assert list(Output(columns=["key"]).columns) == ["key"]


def test_parallel_checks_its_numbers() -> None:
    assert Parallel(cap=10).cap == 10
    assert Parallel(default=key(AcmeSettings).greeting).default == key(AcmeSettings).greeting
    with pytest.raises(ValueError, match="at least 1"):
        Parallel(default=0)
    with pytest.raises(ValueError, match="at least 1"):
        Parallel(cap=0)
    with pytest.raises(ValueError, match="at least 1"):
        Parallel(default=key(AcmeSettings).greeting, unset=0)
    with pytest.raises(ValueError, match="a default read from a setting"):
        Parallel(default=3, unset=8)
    with pytest.raises(ValueError, match="not booleans"):
        Parallel(default=key(AcmeSettings).greeting, unset=True)


def test_annotated_marks_reach_the_field() -> None:
    class Opts(BaseModel):
        repo: Annotated[str, Short("-r")] = Field("x", description="The repo.")

    assert [type(m) for m in Opts.model_fields["repo"].metadata] == [Short]


class Row(BaseModel):
    name: Annotated[str, Key, Column]
    size: int = 0


def _call(annotation: object, **kinds: type[BaseModel]) -> Invocation:
    return Invocation(words=("untaped", "acme", "list"), annotation=annotation, kinds=lambda: kinds)


def test_an_unfilled_output_is_a_table_with_its_declared_columns() -> None:
    output: Output[Row] = Output(columns=("name",))
    assert (output.format, list(output.columns)) == ("table", ["name"])
    output.columns.add("size", "name")
    assert list(output.columns) == ["name", "size"]


def test_a_filled_output_is_a_copy_and_falls_back_to_the_column_marks() -> None:
    default: Output[Row] = Output()
    filled = default._fill({"--format": "json", "--columns": ["+size"]}, _call(Output[Row]))
    filled.columns.add("size")
    filled.empty("none")
    assert (filled.format, list(filled.columns)) == ("json", ["name", "size"])
    assert (default.format, list(default.columns)) == ("table", [])


class Hold(OutcomeRecord):
    """An outcome marking one of its own fields: its inherited ``action`` joins it."""

    holder: str = ""
    item: Annotated[str, Column] = ""


class Bare(OutcomeRecord):
    """An outcome marking none of its own fields."""

    item: str = ""


def test_an_outcomes_action_joins_its_own_column_marks_in_dump_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output()._fill({"--columns": ["?"]}, _call(Output[Hold]))
    assert list(filled.columns) == ["action", "item"]
    filled._print([], kind=None)  # no row to read the columns from: the record names them
    listed = capsys.readouterr()
    assert all(name in listed.out + listed.err for name in ("holder", "item", "action"))


def test_an_outcome_marking_none_of_its_own_fields_shows_every_field() -> None:
    assert default_columns(Bare) == ()


def test_a_single_record_shows_every_field_beside_its_column_marks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output()._fill({}, _call(Output[Row]))
    filled._print(Row(name="a", size=7), kind=None)
    assert "size" in capsys.readouterr().out  # Column marks are a list's columns
    filled._print([Row(name="a", size=7), Row(name="b", size=8)], kind=None)
    assert "size" not in capsys.readouterr().out


class Note(BaseModel):
    key: str
    value: str | None = None


def test_declared_columns_stay_in_a_one_record_table_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output(columns=("key", "value"))._fill({}, _call(Output[Note]))
    filled._print(Note(key="k"), kind=None)  # a detail view would hide the empty value
    assert "value" in capsys.readouterr().out
    filled._print([Note(key="k"), Note(key="j")], kind=None)
    assert "value" not in capsys.readouterr().out  # hide_empty_columns still applies to rows


class Comment(BaseModel):
    author: Annotated[str, Column]
    body: Annotated[str, Column]
    created: str = ""


class Ticket(BaseModel):
    key: Annotated[str, Key]
    summary: str
    comments: Annotated[list[Comment] | None, Following] = None


def test_a_following_field_prints_as_a_second_table_after_the_first(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output()._fill({}, _call(Output[Ticket]))
    filled._print(
        Ticket(key="T-1", summary="sum", comments=[Comment(author="al", body="hi")]), kind=None
    )
    out = capsys.readouterr().out
    assert "\ncomments\n" in out  # the field's name heads its table
    first, second = out.split("author")
    assert "T-1" in first and "hi" not in first
    assert "hi" in second and "created" not in second  # the item's Column marks pick its columns
    filled._print(Ticket(key="T-2", summary="sum"), kind=None)
    assert "author" not in capsys.readouterr().out  # no row carries the field: no second table
    filled._print(Ticket(key="T-3", summary="sum", comments=[]), kind=None)
    assert "No comments." in capsys.readouterr().err  # the empty line, as for the first table


class Linked(Ticket):
    model_config = ConfigDict(extra="allow")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return f"https://t.example/{self.key}"


def test_a_following_field_leaves_every_other_column_to_the_first_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output()._fill({}, _call(Output[Linked]))
    filled._print(Linked(key="T-1", summary="s", more="EXTRA", comments=[]), kind=None)
    first = capsys.readouterr().out.split("\ncomments")[0]
    assert "https://t.example/T-1" in first and "EXTRA" in first  # computed and extra fields


def test_each_row_s_following_table_is_headed_by_its_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output()._fill({}, _call(Output[Ticket]))
    filled._print(
        [
            Ticket(key="T-1", summary="s", comments=[Comment(author="al", body="one")]),
            Ticket(key="T-2", summary="s"),
            Ticket(key="T-3", summary="s", comments=[Comment(author="bo", body="three")]),
        ],
        kind=None,
    )
    out = capsys.readouterr().out
    assert "\ncomments of T-1\n" in out and "\ncomments of T-3\n" in out
    assert "comments of T-2" not in out
    assert out.index("one") < out.index("comments of T-3") < out.index("three")


def test_a_following_field_stays_inside_the_record_outside_a_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled = Output()._fill({"--format": "json"}, _call(Output[Ticket]))
    filled._print(
        Ticket(key="T-1", summary="s", comments=[Comment(author="al", body="hi")]), kind=None
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["comments"] == [{"author": "al", "body": "hi", "created": ""}]


def test_a_renderer_named_after_a_core_format_replaces_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output: Output[Row] = Output(format="raw", render={"raw": lambda row: print(f"<{row.name}>")})
    assert output.formats.count("raw") == 1
    output._fill({}, _call(Output[Row]))._print(Row(name="a"), kind=None)
    assert capsys.readouterr().out == "<a>\n"


def test_columns_print_core_s_table_where_a_renderer_replaced_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output: Output[Row] = Output(render={"table": lambda row: print(f"<{row.name}>")})
    output._fill({}, _call(Output[Row]))._print(Row(name="a"), kind=None)
    assert capsys.readouterr().out == "<a>\n"
    output._fill({"--columns": ["name"]}, _call(Output[Row]))._print(Row(name="a"), kind=None)
    printed = capsys.readouterr().out
    assert "<a>" not in printed and "a" in printed


def test_an_unknown_column_is_refused_when_filled(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        Output()._fill({"--columns": ["nope"]}, _call(Output[Row]))
    assert exited.value.code == 2
    assert "unknown column 'nope'" in capsys.readouterr().err
    banner: Output[Row] = Output(render={"banner": print})
    with pytest.raises(UsageError, match="does not apply to --format banner"):
        banner._fill({"--format": "banner", "--columns": ["name"]}, _call(Output[Row]))


class Other(BaseModel):
    name: Annotated[str, Key]


class Narrower(Row):
    pass


def test_a_filled_stdin_accepts_its_models_and_their_subclasses() -> None:
    kinds = {"acme.row": Row, "acme.narrower": Narrower, "acme.other": Other}
    one = Stdin()._fill({"--stdin": True}, _call(Stdin[Row], **kinds))
    assert sorted(one._kinds) == ["acme.narrower", "acme.row"]
    both = Stdin()._fill({}, _call(Stdin[Row | Other], **kinds))
    assert sorted(both._kinds) == sorted(kinds)
    anything = Stdin()._fill({}, _call(Stdin[Record], **kinds))
    assert anything._any and sorted(anything._kinds) == sorted(kinds)


def test_a_filled_stdin_names_the_kind_each_row_was_read_as() -> None:
    kinds = {"acme.row": Row, "acme.narrower": Narrower, "acme.other": Other}
    anything = Stdin()._fill({}, _call(Stdin[Record], **kinds))
    assert anything.kind(Narrower(name="a")) == "acme.narrower"
    assert anything.kind(Other(name="a")) == "acme.other"
    rows = Stdin()._fill({}, _call(Stdin[Row], **kinds))
    assert rows.kind(Other(name="a")) is None


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


def test_an_unfilled_confirm_never_goes_ahead_unasked() -> None:
    confirm = Confirm()
    with pytest.raises(NeedsYesError, match=r'this command requires --yes .* it asks "Go\?"'):
        confirm("Go?")
    assert confirm("Go?", needed=False) is True


def test_a_confirm_filled_with_yes_goes_ahead() -> None:
    confirm = Confirm()._fill({"--yes": True}, _call(Confirm))
    assert (confirm.yes, confirm.dry_run, confirm("Go?")) == (True, False, True)


def test_unfilled_refresh_parallel_and_limit_read_their_defaults() -> None:
    assert Refresh(object()).directive is None
    assert Parallel(default=2).value == 2
    assert Parallel(default=9, cap=4)._fill({"--parallel": 3}, _call(Parallel)).value == 3
    with pytest.raises(UsageError, match="--limit: must be at least 1"):
        Limit()._fill({"--limit": 0}, _call(Limit))
    assert (Limit().value, Limit(default=5).value) == (None, 5)
    assert Limit(default=5, cap=9)._fill({"--limit": 9}, _call(Limit)).value == 9


@pytest.mark.parametrize(
    "knobs",
    [{"default": 0}, {"cap": 0}, {"default": 10, "cap": 9}, {"cap": True}],
    ids=["default-0", "cap-0", "default-over-cap", "boolean"],
)
def test_a_limit_s_default_and_cap_are_numbers_the_default_within_the_cap(
    knobs: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="Limit's"):
        Limit(**knobs)
