"""``resolve``: one precedence, one refusal, every missing name at once; ``terminal_ask``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from untaped.sdk.prompt import PromptChoice
from untaped_values.api import Input, InputError, Vars, resolve, terminal_ask


class _Answers:
    """An ``Ask`` that answers from a dict and records what it was asked."""

    def __init__(self, **answers: object) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str]] = []

    def __call__(self, name: str, spec: Input, *, label: str) -> object:
        self.asked.append((name, label))
        return self.answers[name]


class _Optional(Input):
    """A consumer's input that may stay without a value (recipe's ``required: false``)."""

    def is_required(self) -> bool:
        return False


def test_the_first_given_mapping_wins_then_the_default_then_an_answer() -> None:
    specs = {
        "flag": Input(type="int"),
        "file": Input(type="int"),
        "default": Input(type="int", default="3"),
        "asked": Input(type="int"),
    }
    ask = _Answers(asked="4")

    resolved = resolve(
        specs, [{"flag": "1"}, {"flag": "9", "file": "2", "default": "8"}], ask=ask, label="t"
    )

    assert resolved.values == {"flag": 1, "file": 2, "default": 8, "asked": 4}
    assert resolved.asked == ("asked",)
    assert resolved.missing == ()
    assert ask.asked == [("asked", "t")]


def test_the_default_applies_when_nothing_is_given() -> None:
    assert resolve({"n": Input(type="int", default="3")}, []).values == {"n": 3}


def test_an_input_that_needs_no_value_is_left_out() -> None:
    assert resolve({"opt": _Optional()}, [], ask=_Answers()).values == {}


def test_a_name_nothing_declares_is_refused_naming_the_declared_ones() -> None:
    with pytest.raises(InputError, match=r"^unknown input: typo; declared: a, b$"):
        resolve({"a": Input(), "b": Input(default="x")}, [{"a": "1"}, {"typo": "x"}])
    with pytest.raises(InputError, match=r"^unknown inputs: x, y; declared: \(none\)$"):
        resolve({}, [{"y": 1, "x": 2}])


def test_a_name_another_file_in_the_run_declares_is_accepted_and_ignored() -> None:
    resolved = resolve({"a": Input()}, [{"a": "1", "other": "2"}], known=["other"])
    assert resolved.values == {"a": "1"}


def test_without_ask_every_missing_name_is_listed_at_once() -> None:
    specs = {"a": Input(), "b": Input(default="x"), "c": Input()}
    resolved = resolve(specs, [])
    assert resolved.missing == ("a", "c")
    assert resolved.values == {"b": "x"}


def test_nothing_is_asked_when_one_missing_input_cannot_be() -> None:
    ask = _Answers(a="1")
    resolved = resolve({"a": Input(), "cols": Input(type="list")}, [], ask=ask)
    assert resolved.missing == ("a", "cols")
    assert ask.asked == []


def test_an_answer_is_converted_and_a_bad_one_names_the_input() -> None:
    with pytest.raises(InputError, match="input 'n': cannot coerce value to int"):
        resolve({"n": Input(type="int")}, [], ask=_Answers(n="x"))


@dataclass
class _Terminal:
    """Just enough of a terminal for ``terminal_ask``."""

    can_prompt: bool = True
    answer: str = "typed"
    asked: list[tuple[str, str]] = field(default_factory=list)

    def text(self, message: str, **_: Any) -> str:
        self.asked.append(("text", message))
        return self.answer

    def secret(self, message: str, **_: Any) -> str:
        self.asked.append(("secret", message))
        return self.answer

    def select(self, message: str, choices: Sequence[PromptChoice[Any]], **_: Any) -> Any:
        self.asked.append(("select", message))
        return choices[-1].value


def test_terminal_ask_is_none_without_a_terminal_or_with_non_interactive() -> None:
    assert terminal_ask(_Terminal(can_prompt=False), Vars()) is None  # type: ignore[arg-type]
    assert terminal_ask(_Terminal(), Vars(non_interactive=True)) is None  # type: ignore[arg-type]


def test_terminal_ask_hides_a_secret_and_picks_a_choice() -> None:
    terminal = _Terminal()
    ask = terminal_ask(terminal, Vars())  # type: ignore[arg-type]
    assert ask is not None

    assert ask("owner", Input(description="Owning team."), label="") == "typed"
    assert ask("token", Input(secret=True), label="/work/api") == "typed"
    assert ask("env", Input(type="choice", choices=("dev", "prod")), label="") == "prod"

    assert terminal.asked == [
        ("text", "owner (Owning team.)"),
        ("secret", "token for /work/api"),
        ("select", "env"),
    ]
