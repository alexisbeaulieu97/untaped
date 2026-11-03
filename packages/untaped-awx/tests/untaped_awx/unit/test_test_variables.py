"""Suite variables through the values plugin: --var > --vars-file > default > prompt."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from untaped.sdk import ConfigError, Terminal, UsageError
from untaped.sdk.prompt import PromptChoice
from untaped_awx.application.suites.loader import LoadTestSuite
from untaped_awx.domain.suites.suite import Suite, VariableSpec
from untaped_awx.infrastructure.suites import DefaultParser
from untaped_values.api import Ask, Input, InputError, Vars, terminal_ask

_PATH = Path("/virtual/vars.yml")
_BODY = "---\nkind: AwxTestSuite\njobTemplate: J\ncases: {c: {launch: {limit: x}}}\n"


class _Files:
    def __init__(self, text: str) -> None:
        self._text = text

    def read_text(self, path: Path) -> str:
        return self._text


class _Answers:
    """An ``Ask`` that answers from a dict and records each name it was asked."""

    def __init__(self, **answers: str) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, name: str, spec: Input, *, label: str) -> object:
        self.asked.append(name)
        return self.answers[name]


def _suite(
    variables: str,
    *,
    given: dict[str, object] | None = None,
    ask: _Answers | None = None,
    known: Sequence[str] = (),
) -> Suite:
    loader = LoadTestSuite(
        _Files(f"---\nvariables:\n{variables}{_BODY}"), parser=DefaultParser(), ask=ask
    )
    return loader(_PATH, given=given, known=known)


def _values(
    variables: str,
    *,
    given: dict[str, object] | None = None,
    ask: Ask | None = None,
    known: Sequence[str] = (),
) -> dict[str, object]:
    """The resolved values, read back from a suite with one case per variable echoing it."""
    names = list(yaml.safe_load(variables))
    cases = "".join(
        f"  {n}: {{launch: {{extra_vars: {{v: {{{{ {n} | to_json }}}}}}}}}}\n" for n in names
    )
    text = f"---\nvariables:\n{variables}---\nkind: AwxTestSuite\njobTemplate: J\ncases:\n{cases}"
    suite = LoadTestSuite(_Files(text), parser=DefaultParser(), ask=ask)(
        _PATH, given=given, known=known
    )
    return {name: suite.cases[name].launch["extra_vars"]["v"] for name in names}


def test_var_wins_over_the_default_and_nothing_is_asked() -> None:
    ask = _Answers()
    assert _values("  env: {default: dev}\n", given={"env": "prod"}, ask=ask) == {"env": "prod"}
    assert ask.asked == []


def test_the_default_applies_when_nothing_is_given() -> None:
    assert _values("  env: {default: dev}\n") == {"env": "dev"}


def test_a_required_variable_is_asked_when_nothing_gives_it() -> None:
    ask = _Answers(env="staging")
    assert _values("  env: {}\n", ask=ask) == {"env": "staging"}
    assert ask.asked == ["env"]


def test_without_an_ask_every_missing_variable_is_named_at_once() -> None:
    with pytest.raises(UsageError) as caught:
        _suite("  env: {}\n  tag: {}\n")
    assert str(caught.value) == (
        "/virtual/vars.yml: required 2 variables not provided: env, tag; "
        "set them with --var NAME=VALUE or run interactively"
    )
    assert caught.value.exit_code == 2


def test_vars_file_values_are_overridden_by_var(tmp_path: Path) -> None:
    first = tmp_path / "a.yml"
    first.write_text("env: file_env\nport: 8080\n")
    second = tmp_path / "b.yml"
    second.write_text("env: later_file\n")
    vars = Vars.model_validate({"vars_file": [first, second]})
    assert _values(
        "  env: {default: dev}\n  port: {type: int, default: 80}\n", given=vars.values()
    ) == {"env": "later_file", "port": 8080}
    vars = Vars.model_validate({"vars_file": [first], "var": [("env", "cli_env")]})
    assert _values("  env: {}\n  port: {type: int}\n", given=vars.values()) == {
        "env": "cli_env",
        "port": 8080,
    }


@pytest.mark.parametrize(
    ("variable", "given", "expected"),
    [
        ("{type: int, default: '8080'}", None, 8080),
        ("{type: int}", "8080", 8080),
        *(("{type: bool}", word, True) for word in ("true", "TRUE", "yes", "on", "1")),
        *(("{type: bool}", word, False) for word in ("false", "no", "off", "0")),
        ("{type: list}", "us-east-1,eu-west-1", ["us-east-1", "eu-west-1"]),
        ("{type: list, default: 'us-east-1,eu-west-1'}", None, ["us-east-1", "eu-west-1"]),
        # A YAML list keeps its elements as written; items converts them.
        ("{type: list}", [80, {"host": "a"}], [80, {"host": "a"}]),
        ("{type: list, items: int}", "80,443", [80, 443]),
        ("{type: choice, choices: [dev, prod]}", "prod", "prod"),
    ],
)
def test_a_given_value_is_converted_to_the_variable_type(
    variable: str, given: object, expected: object
) -> None:
    values = _values(f"  v: {variable}\n", given=None if given is None else {"v": given})
    assert values == {"v": expected}


def test_a_choice_outside_its_choices_is_refused_naming_the_file_and_variable() -> None:
    with pytest.raises(InputError) as caught:
        _suite("  env: {type: choice, choices: [dev, prod]}\n", given={"env": "staging"})
    assert str(caught.value) == (
        "/virtual/vars.yml: input 'env': value is not one of ['dev', 'prod']"
    )


def test_a_name_no_suite_in_the_run_declares_is_a_usage_error() -> None:
    with pytest.raises(UsageError, match="unknown input: frooks; declared: env") as caught:
        _suite("  env: {default: dev}\n", given={"frooks": "x"})
    assert caught.value.exit_code == 2


def test_a_name_another_suite_declares_is_accepted_and_ignored() -> None:
    assert _values("  env: {default: dev}\n", given={"region": "us"}, known=["region"]) == {
        "env": "dev"
    }
    with pytest.raises(UsageError, match="enviornment"):
        _suite("  env: {default: dev}\n", given={"enviornment": "prod"}, known=["region"])


def test_a_bad_declaration_names_the_variable() -> None:
    with pytest.raises(ConfigError, match="variable 'env'"):
        _suite("  env: {type: choice, choices: [a, b], default: c}\n")


def test_variable_spec_is_required_without_a_default() -> None:
    assert VariableSpec(name="env").required is True
    assert VariableSpec(name="env", default="dev").required is False


@dataclass
class _Terminal:
    """Just enough of a terminal for ``terminal_ask``."""

    can_prompt: bool = True
    asked: list[tuple[str, str]] = field(default_factory=list)

    def text(self, message: str, **_: Any) -> str:
        self.asked.append(("text", message))
        return "a,b"

    def secret(self, message: str, **_: Any) -> str:
        self.asked.append(("secret", message))
        return "s3cret"

    def select(self, message: str, choices: Sequence[PromptChoice[Any]], **_: Any) -> Any:
        self.asked.append(("select", message))
        return choices[-1].value


def test_each_variable_gets_its_prompt_naming_the_file() -> None:
    terminal = _Terminal()
    ask = terminal_ask(cast(Terminal, terminal), Vars())
    values = _values(
        "  env: {type: choice, choices: [dev, prod], description: Environment}\n"
        "  token: {secret: true}\n"
        "  regions: {type: list}\n",
        ask=ask,
    )
    assert values == {"env": "prod", "token": "s3cret", "regions": ["a", "b"]}
    assert terminal.asked == [
        ("select", "env for vars.yml (Environment)"),
        ("secret", "token for vars.yml"),
        ("text", "regions for vars.yml (comma-separated)"),
    ]


@pytest.mark.parametrize(("can_prompt", "non_interactive"), [(False, False), (True, True)])
def test_no_terminal_or_non_interactive_never_asks(can_prompt: bool, non_interactive: bool) -> None:
    terminal = _Terminal(can_prompt=can_prompt)
    ask = terminal_ask(cast(Terminal, terminal), Vars(non_interactive=non_interactive))
    assert ask is None
    with pytest.raises(UsageError, match="required 1 variable not provided: env"):
        _suite("  env: {}\n")
    assert terminal.asked == []
