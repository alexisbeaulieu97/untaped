"""Type tokens and default spellings."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Annotated, Literal, TypeVar

import pytest
from pydantic import BaseModel, Field

from untaped.surface._types import (
    COMPUTED,
    annotation_text,
    choice_text,
    default_text,
    factory_default,
    literal_values,
    type_token,
)


class Repo(BaseModel):
    name: str


_Bounded = TypeVar("_Bounded", bound=int)
_Free = TypeVar("_Free")


class Color(enum.Enum):
    RED = "red"


@pytest.mark.parametrize(
    ("annotation", "token"),
    [
        (str, "string"),
        (int, "integer"),
        (float, "number"),
        (bool, "boolean"),
        (dict[str, int], "object"),
        (list[str], "string[]"),
        (list[Repo], "Repo[]"),
        (Repo, "Repo"),
        (str | None, "string?"),
        (int | str, "integer|string"),
        (str | int, "integer|string"),
        (Annotated[str, Field(min_length=1, max_length=64)], "string(maxLength=64,minLength=1)"),
        (Annotated[int, Field(ge=0)], "integer(minimum=0)"),
        (Annotated[list[str], Field(min_length=1)], "string[](minItems=1)"),
        (Literal["a", "b"], "string"),
        (Literal["a"] | None, "string?"),
    ],
)
def test_type_tokens(annotation: object, token: str) -> None:
    assert type_token(annotation).token == token


def test_a_type_variable_reads_as_its_bound_or_object() -> None:
    assert type_token(list[_Bounded]).token == "integer[]"
    assert type_token(list[_Free] | None).token == "array?"
    assert annotation_text(dict[str, _Bounded]) == "dict[str, int]"
    assert annotation_text(_Free) == "object"


def test_literal_values_are_choices_not_part_of_the_token() -> None:
    assert type_token(Literal["heads", "tags"] | None).choices == ("heads", "tags")
    assert type_token(str).choices == ()
    assert literal_values(Annotated[Literal[1, 2] | None, "x"]) == (1, 2)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "None"),
        (True, "true"),
        (False, "false"),
        (50, "50"),
        (1.5, "1.5"),
        ("heads", "heads"),
        ("a b", '"a b"'),
        ("a=b", '"a=b"'),
        ("", '""'),
        ([], "[]"),
        (["b", "a"], '["b","a"]'),
        ({"b": 1, "a": {2}}, '{"a":[2],"b":1}'),
        (Color.RED, "Color.RED"),
        (object(), COMPUTED),
        (factory_default(list), "[]"),
        (factory_default(lambda: []), COMPUTED),
    ],
)
def test_default_spellings(value: object, text: str) -> None:
    assert default_text(value) == text


def test_none_reads_unset_in_cli_and_cfg_and_named_objects_by_their_names() -> None:
    marker = object()
    assert default_text(None, none="unset") == "unset"
    assert default_text(marker, names={id(marker): "NO_PROGRESS"}) == "NO_PROGRESS"


def test_a_path_under_home_reads_the_same_on_every_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_text(tmp_path / ".untaped" / "repos") == "~/.untaped/repos"
    assert default_text(str(tmp_path)) == "~"
    assert default_text(Path("/srv/repos")) == "/srv/repos"


def test_choice_spellings() -> None:
    assert choice_text("json") == "json"
    assert choice_text("two words") == '"two words"'
    assert choice_text(3) == "3"
