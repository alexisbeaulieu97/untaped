"""Type tokens and default spellings."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypeVar

import pytest
from pydantic import BaseModel, Field

from untaped.surface._contracts import model_lines
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


def test_an_array_of_a_union_reads_apart_from_a_union_holding_an_array() -> None:
    assert type_token(list[int | str]).token == "(integer|string)[]"
    assert type_token(int | list[str]).token == "integer|string[]"
    assert type_token(list[int | None]).token == "integer?[]"
    assert type_token(list[list[Choice[int | str]]]).token == "Choice[integer|string][][]"
    assert type_token(list[Choice[Literal["(("]] | int]).token == (
        '(Choice[string(choice="((")]|integer)[]'
    )
    assert type_token(list[Choice[Literal['q"(']]]).token == 'Choice[string(choice="q\\"(")][]'


def test_a_type_variable_reads_as_its_bound_or_object() -> None:
    assert type_token(list[_Bounded]).token == "integer[]"
    assert type_token(list[_Free] | None).token == "array?"
    assert annotation_text(dict[str, _Bounded]) == "dict[str, int]"
    assert annotation_text(_Free) == "object"


@dataclass
class Choice[T]:
    value: T


class Page[T](BaseModel):
    items: list[T]


def test_a_generic_reads_as_name_and_bound_arguments() -> None:
    assert type_token(list[Choice[_Free]]).token == "Choice[object][]"
    assert type_token(list[Choice[_Bounded]] | None).token == "Choice[integer][]?"
    assert type_token(Choice[int]).token == "Choice[integer]"
    assert type_token(list[Choice[int] | Choice[str]]).token == "(Choice[integer]|Choice[string])[]"
    assert type_token(Choice[Literal["b", "a"]]).token == "Choice[string(choice=a|b)]"
    assert type_token(Choice[Literal["a", "c|d"]]).token == 'Choice[string(choice="c|d"|a)]'
    assert type_token(Choice[str | None]).token == "Choice[string?]"


def test_a_generic_pydantic_model_reads_the_same_way() -> None:
    assert type_token(Page[str]).token == "Page[string]"
    assert type_token(Page[_Bounded]).token == "Page[integer]"
    assert type_token(list[Page[_Free]]).token == "Page[object][]"
    assert type_token(Page).token == "Page[object]"


type Choices = list[Choice[int]]
type Pair[X] = list[Choice[X]]


@dataclass
class Box[T]:
    value: T


type Swapped[K, V] = list[Choice[V] | Box[K]]
type Unused[K, V] = list[Choice[V]]
type Recursive = list[Recursive] | Choice[int]
type RecursiveOf[X] = list[RecursiveOf[X]] | Choice[X]


class Aliased(BaseModel):
    plain: Choices
    paired: Pair[str]


def test_a_generic_inside_a_type_alias_reads_the_same_way() -> None:
    assert type_token(Choices).token == "Choice[integer][]"
    assert type_token(Pair[int]).token == "Choice[integer][]"
    assert type_token(Swapped[int, str]).token == "(Box[integer]|Choice[string])[]"
    assert type_token(Unused[int, str]).token == "Choice[string][]"
    assert {line.text for line in model_lines("contract o.Aliased", Aliased, None)} >= {
        "contract o.Aliased.plain Choice[integer][]",
        "contract o.Aliased.paired Choice[string][]",
    }


def test_a_recursive_alias_reads_by_its_name_where_it_comes_back() -> None:
    assert type_token(Recursive).token == "Choice[integer]|Recursive[]"
    assert type_token(RecursiveOf[int]).token == "Choice[integer]|RecursiveOf[integer][]"


class Node(BaseModel):
    kids: list[Page[Node]]


class Leaf(BaseModel):
    page: Page[list[Leaf]] | None = None


class Tree(BaseModel):
    name: str
    children: list[Tree] = []


class Holds[T](BaseModel):
    choices: list[Choice[T]]
    page: Page[T] | None = None


def test_a_model_that_refers_to_itself_reads_by_its_name() -> None:
    assert type_token(Node).token == "Node"
    assert type_token(Leaf).token == "Leaf"
    assert type_token(Page[Page[str]]).token == "Page[Page[string]]"
    assert {line.text for line in model_lines("contract o.Node", Node, None)} == {
        "contract o.Node(kids)",
        "contract o.Node.kids Page[Node][]",
    }
    assert "contract o.Leaf.page Page[Leaf[]]? default=None" in {
        line.text for line in model_lines("contract o.Leaf", Leaf, None)
    }


def test_a_self_referential_payload_model_keeps_its_fields() -> None:
    assert [line.text for line in model_lines("contract o.Tree", Tree, None)] == [
        "contract o.Tree(name)",
        "contract o.Tree.children Tree[] default=[]",
        "contract o.Tree.name string",
    ]


def test_a_generic_payload_model_reads_its_own_parameters_as_object() -> None:
    assert {line.text for line in model_lines("contract o.Holds", Holds, None)} >= {
        "contract o.Holds.choices Choice[object][]",
        "contract o.Holds.page Page[object]? default=None",
    }


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


def test_a_payload_field_spells_a_generic_like_a_parameter() -> None:
    class Holder(BaseModel):
        choices: list[Choice[int]]

    assert "contract o.Holder.choices Choice[integer][]" in {
        line.text for line in model_lines("contract o.Holder", Holder, None)
    }
