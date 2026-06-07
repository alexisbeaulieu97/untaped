"""Unit tests for typed prompt primitives."""

from __future__ import annotations

import io
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

import pytest

from untaped.errors import ConfigError
from untaped.prompts import PromptToolkitPromptBackend
from untaped.ui import PromptChoice, UiContext

T = TypeVar("T")


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class FakePromptBackend:
    def __init__(
        self,
        *,
        confirm: bool = True,
        text: str = "text-value",
        secret: str = "secret-value",
        select: object = "selected-value",
        multiselect: list[object] | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self.confirm_value = confirm
        self.text_value = text
        self.secret_value = secret
        self.select_value = select
        self.multiselect_value = multiselect or []
        self.exc = exc
        self.calls: list[str] = []

    def confirm(self, message: str, *, default: bool) -> bool:
        self.calls.append(f"confirm:{message}:{default}")
        if self.exc is not None:
            raise self.exc
        return self.confirm_value

    def text(self, message: str, *, default: str | None) -> str:
        self.calls.append(f"text:{message}:{default}")
        if self.exc is not None:
            raise self.exc
        return self.text_value

    def secret(self, message: str, *, confirmation: bool) -> str:
        self.calls.append(f"secret:{message}:{confirmation}")
        if self.exc is not None:
            raise self.exc
        return self.secret_value

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None,
        search: bool,
    ) -> T:
        self.calls.append(f"select:{message}:{default}:{search}")
        if self.exc is not None:
            raise self.exc
        return self.select_value  # type: ignore[return-value]

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        defaults: Sequence[T],
    ) -> list[T]:
        self.calls.append(f"multiselect:{message}:{list(defaults)}")
        if self.exc is not None:
            raise self.exc
        return self.multiselect_value  # type: ignore[return-value]


def test_prompt_primitives_return_typed_backend_values() -> None:
    backend = FakePromptBackend(select=2, multiselect=[1, 3])
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=backend)
    choices = [
        PromptChoice(value=1, label="one"),
        PromptChoice(value=2, label="two"),
        PromptChoice(value=3, label="three"),
    ]

    assert ui.confirm("continue?", default=True) is True
    assert ui.text("name", default="default") == "text-value"
    assert ui.secret("token", confirmation=True) == "secret-value"
    assert ui.select("pick", choices, default=1, search=True) == 2
    assert ui.multiselect("pick many", choices, defaults=[1], min_count=1) == [1, 3]

    assert backend.calls == [
        "confirm:continue?:True",
        "text:name:default",
        "secret:token:True",
        "select:pick:1:True",
        "multiselect:pick many:[1]",
    ]


@pytest.mark.parametrize("method_name", ["text", "secret"])
def test_required_text_prompts_reject_empty_values(method_name: str) -> None:
    backend = FakePromptBackend(text="", secret=" ")
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=backend)
    method = getattr(ui, method_name)

    with pytest.raises(ConfigError, match="prompt"):
        method("value")


def test_optional_text_prompts_allow_empty_values() -> None:
    backend = FakePromptBackend(text="", secret="")
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=backend)

    assert ui.text("value", required=False) == ""
    assert ui.secret("value", required=False) == ""


@pytest.mark.parametrize("exc", [EOFError(), KeyboardInterrupt()])
def test_prompt_cancellation_maps_to_config_error(exc: BaseException) -> None:
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=FakePromptBackend(exc=exc))

    with pytest.raises(ConfigError, match="prompt cancelled"):
        ui.text("value")


def test_non_interactive_stdin_fails_before_invoking_backend() -> None:
    backend = FakePromptBackend()
    ui = UiContext(stdin=io.StringIO(), prompt_backend=backend)

    with pytest.raises(ConfigError, match="interactive"):
        ui.confirm("continue?")

    assert backend.calls == []


def test_select_requires_choices_and_preserves_original_value_type() -> None:
    choices = [
        PromptChoice(value=("repo", 1), label="first"),
        PromptChoice(value=("repo", 2), label="second"),
    ]
    backend = FakePromptBackend(select=("repo", 2))
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=backend)

    assert ui.select("repo", choices) == ("repo", 2)

    with pytest.raises(ConfigError, match="at least one choice"):
        ui.select("empty", [])


def test_multiselect_enforces_min_count() -> None:
    choices = [PromptChoice(value="alpha", label="Alpha")]
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=FakePromptBackend(multiselect=[]))

    with pytest.raises(ConfigError, match="at least 1"):
        ui.multiselect("repos", choices, min_count=1)


def test_prompt_toolkit_select_uses_stderr_session_and_returns_typed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdin = TtyStringIO()
    stderr = TtyStringIO()
    choices = [
        PromptChoice(value=("repo", 1), label="first", description="primary"),
        PromptChoice(value=("repo", 2), label="second"),
    ]
    seen: dict[str, Any] = {}

    monkeypatch.setattr("untaped.prompts.create_input", lambda stream: ("input", stream))
    monkeypatch.setattr("untaped.prompts.create_output", lambda stream: ("output", stream))

    @contextmanager
    def _app_session(**kwargs: object) -> Any:
        seen["app_session"] = kwargs
        yield

    def _choice(**kwargs: object) -> int:
        seen["choice"] = kwargs
        return 0

    monkeypatch.setattr("untaped.prompts.create_app_session", _app_session)
    monkeypatch.setattr("untaped.prompts.choice", _choice)

    selected = PromptToolkitPromptBackend(stdin=stdin, stderr=stderr).select(
        "Pick repo",
        choices,
        default=("repo", 2),
        search=False,
    )

    assert selected == ("repo", 1)
    assert seen["app_session"] == {
        "input": ("input", stdin),
        "output": ("output", stderr),
    }
    assert seen["choice"] == {
        "message": "Pick repo:",
        "options": [(0, "first - primary"), (1, "second")],
        "default": 1,
        "style": None,
    }


def test_prompt_toolkit_multiselect_handles_cancelled_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("untaped.prompts.create_input", lambda stream: stream)
    monkeypatch.setattr("untaped.prompts.create_output", lambda stream: stream)

    @contextmanager
    def _app_session(**_: object) -> Any:
        yield

    class _Dialog:
        def run(self) -> None:
            return None

    monkeypatch.setattr("untaped.prompts.create_app_session", _app_session)
    monkeypatch.setattr(
        "untaped.prompts.checkboxlist_dialog",
        lambda **_: _Dialog(),
    )

    backend = PromptToolkitPromptBackend(stdin=TtyStringIO(), stderr=TtyStringIO())

    with pytest.raises(ConfigError, match="prompt cancelled"):
        backend.multiselect("Pick repos", [PromptChoice(value="one", label="One")], defaults=[])
