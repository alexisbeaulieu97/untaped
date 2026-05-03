"""Variable resolution: CLI > vars_files > default > prompt."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_awx.application.test.ports import Prompt
from untaped_awx.domain.test_suite import VariableSpec
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.test.vars_resolver import resolve_variables


class StubPrompt(Prompt):
    """In-memory replacement for the interactive prompt."""

    def __init__(self, *, answers: dict[str, str], interactive: bool = True) -> None:
        self._answers = dict(answers)
        self._interactive = interactive
        self.calls: list[VariableSpec] = []

    def is_interactive(self) -> bool:
        return self._interactive

    def ask(self, spec: VariableSpec) -> str:
        self.calls.append(spec)
        if spec.name not in self._answers:
            raise KeyError(f"no stub answer for {spec.name}")
        return self._answers[spec.name]


def _spec(name: str, **kwargs: object) -> VariableSpec:
    return VariableSpec(name=name, **kwargs)  # type: ignore[arg-type]


def test_cli_overrides_default_and_prompt() -> None:
    prompt = StubPrompt(answers={})
    specs = {"env": _spec("env", default="dev")}
    values = resolve_variables(specs, cli={"env": "prod"}, files=(), prompt=prompt)
    assert values == {"env": "prod"}
    assert prompt.calls == []  # CLI value wins; no prompt


def test_default_used_when_no_cli_or_file() -> None:
    prompt = StubPrompt(answers={})
    specs = {"env": _spec("env", default="dev")}
    values = resolve_variables(specs, cli={}, files=(), prompt=prompt)
    assert values == {"env": "dev"}
    assert prompt.calls == []  # default present; no prompt


def test_prompt_used_when_required_and_no_other_source() -> None:
    prompt = StubPrompt(answers={"env": "staging"})
    specs = {"env": _spec("env")}  # no default → required
    values = resolve_variables(specs, cli={}, files=(), prompt=prompt)
    assert values == {"env": "staging"}
    assert [c.name for c in prompt.calls] == ["env"]


def test_non_interactive_with_missing_required_raises() -> None:
    prompt = StubPrompt(answers={}, interactive=False)
    specs = {"env": _spec("env"), "tag": _spec("tag")}
    with pytest.raises(AwxApiError, match="env"):
        resolve_variables(specs, cli={}, files=(), prompt=prompt)


def test_vars_file_overrides_default(tmp_path: Path) -> None:
    f = tmp_path / "vars.yml"
    f.write_text("env: file_env\nport: 8080\n")
    prompt = StubPrompt(answers={})
    specs = {
        "env": _spec("env", default="dev"),
        "port": _spec("port", type="int", default=80),
    }
    values = resolve_variables(specs, cli={}, files=(f,), prompt=prompt)
    assert values == {"env": "file_env", "port": 8080}


def test_cli_overrides_vars_file(tmp_path: Path) -> None:
    f = tmp_path / "vars.yml"
    f.write_text("env: file_env\n")
    prompt = StubPrompt(answers={})
    specs = {"env": _spec("env")}
    values = resolve_variables(specs, cli={"env": "cli_env"}, files=(f,), prompt=prompt)
    assert values == {"env": "cli_env"}


def test_int_type_coerces_string() -> None:
    prompt = StubPrompt(answers={})
    specs = {"port": _spec("port", type="int")}
    values = resolve_variables(specs, cli={"port": "8080"}, files=(), prompt=prompt)
    assert values == {"port": 8080}


def test_bool_type_coerces_string() -> None:
    prompt = StubPrompt(answers={})
    specs = {"flag": _spec("flag", type="bool")}
    for raw, expected in (("true", True), ("FALSE", False), ("1", True), ("0", False)):
        values = resolve_variables(specs, cli={"flag": raw}, files=(), prompt=prompt)
        assert values == {"flag": expected}, raw


def test_list_type_splits_csv() -> None:
    prompt = StubPrompt(answers={})
    specs = {"regions": _spec("regions", type="list")}
    values = resolve_variables(
        specs, cli={"regions": "us-east-1,eu-west-1"}, files=(), prompt=prompt
    )
    assert values == {"regions": ["us-east-1", "eu-west-1"]}


def test_choice_rejects_invalid_value() -> None:
    prompt = StubPrompt(answers={})
    specs = {"env": _spec("env", type="choice", choices=("dev", "prod"))}
    with pytest.raises(AwxApiError, match="env"):
        resolve_variables(specs, cli={"env": "staging"}, files=(), prompt=prompt)


def test_unknown_variable_in_cli_raises() -> None:
    """Pass-through for unknown vars would silently swallow user typos."""
    prompt = StubPrompt(answers={})
    specs = {"env": _spec("env", default="dev")}
    with pytest.raises(AwxApiError, match="frooks"):
        resolve_variables(specs, cli={"frooks": "x"}, files=(), prompt=prompt)


@pytest.fixture
def _no_real_prompt(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Guard: the test module never calls the real TyperPrompt."""
    yield
