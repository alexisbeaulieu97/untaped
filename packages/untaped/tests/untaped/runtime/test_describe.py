"""The describer: types, fields and methods in the words every ``untaped docs`` page uses."""

from __future__ import annotations

import enum
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, JsonValue, SecretStr

from untaped.runtime.describe import (
    fields_of,
    first_paragraph,
    method_decl,
    models_in,
    noun,
    type_text,
)

type Tree = dict[str, Tree] | str
type RepoName = Annotated[str, "owner/name"]


class Tone(enum.Enum):
    WARM = "warm"
    COOL = "cool"


class JobResult(BaseModel):
    """A result."""


@pytest.mark.parametrize(
    ("annotation", "words"),
    [
        (str, "text"),
        (int | None, "integer or null"),
        (list[Path], "list of path"),
        (dict[str, timedelta], "map of name to duration"),
        (SecretStr, "secret"),
        (Tone, "`warm`, `cool`"),
        (Tone | None, "`warm`, `cool`, or null"),
        (Literal[True, None, 3, "x"], "`true`, `null`, `3`, `x`"),
        (RepoName, "text"),
        (Annotated[int, "mark"] | None, "integer or null"),
        (list[JobResult], "list of job result"),
        (JsonValue, "any JSON value"),
        (dict[str, list[JsonValue]] | None, "map of name to list of any JSON value or null"),
        (Tree, "map of name to tree or text"),
    ],
)
def test_types_read_in_a_reader_s_words(annotation: object, words: str) -> None:
    assert type_text(annotation) == words


def test_a_recursive_alias_on_a_model_describes_without_end() -> None:
    class Node(BaseModel):
        tree: Tree
        children: list[Node] = []

    tree, children = fields_of(Node)
    assert (tree.name, tree.type, tree.fields) == ("tree", "map of name to tree or text", ())
    assert (children.type, children.holds, children.fields) == ("list of node", "", ())


class Size(BaseModel):
    width: int = Field(0, description="Across.")


class Widget(BaseModel):
    """One widget.

    Kept on a rack.
    """

    name: str = Field(alias="widget-name", description="Its name.")
    size: Size | None = None
    spares: dict[str, Size] = {}


def test_fields_carry_their_alias_default_and_nested_models() -> None:
    name, size, spares = fields_of(Widget)
    assert (name.name, name.required, name.description) == ("widget-name", True, "Its name.")
    assert (size.type, size.holds, size.default) == ("size or null", "", None)
    assert size.fields[0].default == 0
    assert (spares.holds, spares.default) == (".NAME", {})


def test_prose_reads_the_first_paragraph_and_the_class_name() -> None:
    assert first_paragraph(Widget.__doc__ or "") == "One widget."
    assert noun(JobResult) == "job result"


def test_models_in_finds_every_model_a_hint_names() -> None:
    assert models_in(list[Widget] | None) == [Widget]
    assert models_in(dict[str, Size] | Widget) == [Size, Widget]
    assert models_in(str) == []


def test_a_method_is_spelled_as_code_without_module_paths() -> None:
    def lookup(self: object, name: str, limit: int = 3) -> list[Widget] | None:
        """The widgets called ``name``.

        More.
        """
        return None

    hints = {"name": str, "limit": int, "return": list[Widget] | None}
    method = method_decl("lookup", lookup, hints)
    assert method.signature == "lookup(name: str, limit: int) -> list[Widget] | None"
    assert (method.summary, method.required) == ("The widgets called ``name``.", False)
