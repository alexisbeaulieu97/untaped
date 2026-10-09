"""Unit tests for typed prompt primitives."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TypeVar

import pytest

from untaped.errors import ConfigError, OperationCancelledError, PromptInterruptedError, UsageError
from untaped.picker import PickCatalog, Picked, PickItem, PickRequest, PickResult
from untaped.prompts import TerminalPromptBackend, handle_prompt_exception
from untaped.screen.core import Cancel
from untaped.theme import BUILTIN_THEMES, ThemeSpec
from untaped.ui import PromptChoice, UiContext

T = TypeVar("T")


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class FakePromptBackend:
    needs_terminal = False

    def __init__(
        self,
        *,
        confirm: bool = True,
        text: str = "text-value",
        secret: str = "secret-value",
        select: object = "selected-value",
        multiselect: list[object] | None = None,
        pick: PickResult | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self.pick_value = pick
        self.confirm_value = confirm
        self.text_value = text
        self.secret_value = secret
        self.select_value = select
        self.multiselect_value = multiselect or []
        self.exc = exc
        self.calls: list[str] = []

    def pick_many(self, request: PickRequest) -> PickResult | None:
        self.calls.append(f"pick_many:{request.heading}")
        if self.exc is not None:
            raise self.exc
        return self.pick_value

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


@pytest.mark.parametrize("method_name", ["confirm", "text", "secret", "select", "multiselect"])
@pytest.mark.parametrize(
    ("exc", "expected"),
    [(EOFError(), ConfigError), (KeyboardInterrupt(), PromptInterruptedError)],
)
def test_prompt_cancellation_maps_to_its_error(
    method_name: str,
    exc: BaseException,
    expected: type[Exception],
) -> None:
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=FakePromptBackend(exc=exc))
    choices = [PromptChoice(value="one", label="One")]

    with pytest.raises(expected, match="prompt cancelled"):
        match method_name:
            case "confirm":
                ui.confirm("continue?")
            case "text":
                ui.text("value")
            case "secret":
                ui.secret("value")
            case "select":
                ui.select("value", choices)
            case "multiselect":
                ui.multiselect("value", choices)


def test_non_interactive_stdin_fails_before_invoking_backend() -> None:
    backend = FakePromptBackend()
    ui = UiContext(stdin=io.StringIO(), prompt_backend=backend)

    with pytest.raises(UsageError, match="interactive"):
        ui.confirm("continue?")

    assert backend.calls == []


class _DrawingBackend(FakePromptBackend):
    """A fake backend that, like the real one, draws on the streams of its context."""

    needs_terminal = True

    def __init__(self, holder: list[UiContext]) -> None:
        super().__init__()
        self.holder = holder
        self.streams: list[tuple[object, object]] = []

    def confirm(self, message: str, *, default: bool) -> bool:
        self.streams.append((self.holder[0].stdin, self.holder[0].stderr))
        return super().confirm(message, default=default)


def _drawing_ui(
    monkeypatch: pytest.MonkeyPatch, *, stderr: io.StringIO
) -> tuple[UiContext, _DrawingBackend, list[TtyStringIO]]:
    opened: list[TtyStringIO] = []

    def opener(*, write: bool = False) -> TtyStringIO:
        opened.append(TtyStringIO())
        return opened[-1]

    monkeypatch.setattr("untaped.ui.open_controlling_terminal", opener)
    holder: list[UiContext] = []
    backend = _DrawingBackend(holder)
    ui = UiContext(stdin=TtyStringIO(), stderr=stderr, prompt_backend=backend)
    holder.append(ui)
    return ui, backend, opened


def test_a_prompt_draws_on_the_controlling_terminal_when_stderr_is_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = io.StringIO()
    ui, backend, opened = _drawing_ui(monkeypatch, stderr=stderr)
    stdin = ui.stdin

    assert ui.confirm("go?") is True

    assert backend.streams == [(opened[0], opened[1])]
    assert (ui.stdin, ui.stderr) == (stdin, stderr)  # put back
    assert all(handle.closed for handle in opened)


def test_a_prompt_uses_its_streams_when_both_are_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui, backend, opened = _drawing_ui(monkeypatch, stderr=TtyStringIO())

    ui.confirm("go?")

    assert backend.streams == [(ui.stdin, ui.stderr)]
    assert opened == []


def test_a_prompt_with_stderr_redirected_and_no_terminal_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_terminal(*, write: bool = False) -> TtyStringIO:
        raise OSError("no controlling terminal")

    monkeypatch.setattr("untaped.ui.open_controlling_terminal", no_terminal)
    backend = _DrawingBackend([])
    ui = UiContext(stdin=TtyStringIO(), stderr=io.StringIO(), prompt_backend=backend)

    with pytest.raises(UsageError, match="requires a terminal"):
        ui.confirm("go?")

    assert backend.calls == []


def test_a_prompt_with_piped_stdin_is_refused_without_opening_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui, backend, opened = _drawing_ui(monkeypatch, stderr=io.StringIO())
    ui.stdin = io.StringIO("piped")

    with pytest.raises(UsageError, match="TTY on stdin"):
        ui.confirm("go?")

    assert opened == [] and backend.calls == []


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


@contextmanager
def _real_terminal(
    monkeypatch: pytest.MonkeyPatch, keys: str, *, theme: ThemeSpec | None = None
) -> Iterator[TerminalPromptBackend]:
    """A real backend whose terminal reads ``keys`` from a pipe and draws nowhere."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        pipe.close()  # a prompt that is not finished reads EOF instead of hanging
        monkeypatch.setattr("untaped.screen.terminal.create_input", lambda _stream: pipe)
        monkeypatch.setattr("untaped.screen.terminal.create_output", lambda _stream: DummyOutput())
        yield TerminalPromptBackend(stdin=TtyStringIO(), stderr=TtyStringIO(), theme=theme)


_ENTER = "\r"
_DOWN = "\x1b[B"
_CTRL_C = "\x03"
_CTRL_D = "\x04"


def test_terminal_backend_select_returns_the_typed_value_of_the_chosen_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = [
        PromptChoice(value=("repo", 1), label="first", description="primary"),
        PromptChoice(value=("repo", 2), label="second"),
    ]
    with _real_terminal(monkeypatch, _ENTER) as backend:
        # the default starts under the cursor
        assert backend.select("Pick repo", choices, default=("repo", 2), search=False) == (
            "repo",
            2,
        )
    with _real_terminal(monkeypatch, _DOWN + _ENTER) as backend:
        assert backend.select("Pick repo", choices, default=None, search=False) == ("repo", 2)


def test_terminal_backend_search_select_filters_then_returns_the_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = [PromptChoice(value=1, label="alpha"), PromptChoice(value=2, label="beta")]
    with _real_terminal(monkeypatch, "bet" + _ENTER) as backend:
        assert backend.select("Pick", choices, default=None, search=True) == 2


def test_terminal_backend_multiselect_returns_the_checked_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = [PromptChoice(value=n, label=f"n{n}") for n in (1, 2, 3)]
    with _real_terminal(monkeypatch, f" {_DOWN}{_DOWN} {_ENTER}") as backend:
        assert backend.multiselect("Pick", choices, defaults=[2]) == [1, 2, 3]


def test_terminal_backend_multiselect_handles_a_cancelled_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _real_terminal(monkeypatch, _CTRL_D) as backend, pytest.raises(EOFError) as raised:
        backend.multiselect("Pick repos", [PromptChoice(value="one", label="One")], defaults=[])
    mapped = handle_prompt_exception(raised.value)
    assert isinstance(mapped, ConfigError)
    assert str(mapped) == "prompt cancelled"


@pytest.mark.parametrize(
    ("keys", "default", "expected"),
    [("y\r", False, True), ("\r", False, False), ("n\r", True, False), ("\r", True, True)],
)
def test_terminal_backend_confirm_takes_a_typed_answer_over_the_default(
    monkeypatch: pytest.MonkeyPatch, keys: str, default: bool, expected: bool
) -> None:
    with _real_terminal(monkeypatch, keys) as backend:
        assert backend.confirm("Remove alias?", default=default) is expected


def test_terminal_backend_confirm_asks_again_after_an_unknown_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _real_terminal(monkeypatch, "maybe\r\x7f\x7f\x7f\x7f\x7f" + "yes\r") as backend:
        assert backend.confirm("Remove alias?", default=False) is True


def test_terminal_backend_text_returns_the_default_or_what_was_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _real_terminal(monkeypatch, _ENTER) as backend:
        assert backend.text("Name", default="dev") == "dev"
    with _real_terminal(monkeypatch, "\x7f\x7fx y" + _ENTER) as backend:
        assert backend.text("Name", default="dev") == "dx y"


@pytest.mark.parametrize(
    ("keys", "expected"),
    [("abc\rabc\r", "abc"), ("abc\r", "abc")],
)
def test_terminal_backend_secret_returns_the_typed_value(
    monkeypatch: pytest.MonkeyPatch, keys: str, expected: str
) -> None:
    with _real_terminal(monkeypatch, keys) as backend:
        assert backend.secret("Token", confirmation=keys.count("\r") == 2) == expected


def test_terminal_backend_secret_confirmation_mismatch_is_an_invalid_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        _real_terminal(monkeypatch, "abc\rabd\r") as backend,
        pytest.raises(ConfigError, match="prompt values did not match") as raised,
    ):
        backend.secret("Token", confirmation=True)
    assert raised.value.category == "invalid"


@pytest.mark.parametrize("method", ["confirm", "text", "secret", "select", "multiselect"])
def test_terminal_backend_ctrl_c_interrupts_and_ctrl_d_ends_the_prompt(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    choices = [PromptChoice(value="one", label="One")]

    def ask(backend: TerminalPromptBackend) -> object:
        match method:
            case "confirm":
                return backend.confirm("Go?", default=False)
            case "text":
                return backend.text("Name", default=None)
            case "secret":
                return backend.secret("Token", confirmation=False)
            case "select":
                return backend.select("Pick", choices, default=None, search=False)
            case _:
                return backend.multiselect("Pick", choices, defaults=[])

    with _real_terminal(monkeypatch, _CTRL_C) as backend, pytest.raises(KeyboardInterrupt):
        ask(backend)
    with _real_terminal(monkeypatch, _CTRL_D) as backend, pytest.raises(EOFError):
        ask(backend)


def _record(backend: TerminalPromptBackend) -> str:
    stderr = backend.stderr
    assert isinstance(stderr, io.StringIO)
    return stderr.getvalue()


def test_an_answered_prompt_leaves_one_plain_record_line(monkeypatch: pytest.MonkeyPatch) -> None:
    choices = [
        PromptChoice(value=1, label="alpha", description="first"),
        PromptChoice(value=2, label="[bold]beta[/bold]"),
        PromptChoice(value=2, label="gamma"),
    ]
    with _real_terminal(monkeypatch, _ENTER) as backend:
        backend.text("Name", default="dev")
        assert _record(backend) == "Name: dev\n"
    with _real_terminal(monkeypatch, "y\r") as backend:
        backend.confirm("Remove alias?", default=False)
        assert _record(backend) == "Remove alias? [y/N]: y\n"
    with _real_terminal(monkeypatch, _ENTER) as backend:
        backend.confirm("Remove alias?", default=True)
        assert _record(backend) == "Remove alias? [Y/n]: y\n"
    with _real_terminal(monkeypatch, "n\r") as backend:
        backend.confirm("Remove alias?", default=True)
        assert _record(backend) == "Remove alias? [Y/n]: n\n"
    # a label, never its description, and never read as markup
    with _real_terminal(monkeypatch, _DOWN + _ENTER) as backend:
        backend.select("Pick", choices, default=None, search=False)
        assert _record(backend) == "Pick: [bold]beta[/bold]\n"
    with _real_terminal(monkeypatch, "gam" + _ENTER) as backend:
        backend.select("Pick", choices, default=None, search=True)
        assert _record(backend) == "Pick: gamma\n"
    with _real_terminal(monkeypatch, f" {_DOWN}{_DOWN} {_ENTER}") as backend:
        backend.multiselect("Pick", choices, defaults=[])
        assert _record(backend) == "Pick: alpha, gamma\n"
    with _real_terminal(monkeypatch, _ENTER) as backend:
        backend.multiselect("Pick", choices, defaults=[])
        assert _record(backend) == "Pick: \n"


def test_a_secrets_record_line_is_the_mask_never_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _real_terminal(
        monkeypatch, "hunter2\rhunter2\r", theme=BUILTIN_THEMES["plain"]
    ) as backend:
        assert backend.secret("Token", confirmation=True) == "hunter2"
        record = _record(backend)
    assert record == "Token: *******\nConfirm value: *******\n"
    assert "hunter2" not in record
    with _real_terminal(monkeypatch, "hunter2\r") as backend:
        backend.secret("Token", confirmation=False)
        record = _record(backend)
    assert record == "Token: \u2022\u2022\u2022\u2022\u2022\u2022\u2022\n"
    assert "hunter2" not in record


def test_a_record_line_drops_control_characters() -> None:
    from untaped.screen.core import Quit

    backend = TerminalPromptBackend(stdin=TtyStringIO(), stderr=TtyStringIO())
    backend.run_screen = lambda screen, *, theme: Quit("a\x1b[31mb\r")  # type: ignore[method-assign,assignment]
    assert backend.text("Name", default=None) == "a\x1b[31mb\r"
    assert _record(backend) == "Name: a[31mb\n"


@pytest.mark.parametrize("method", ["confirm", "text", "secret", "select", "multiselect"])
@pytest.mark.parametrize("keys", [_CTRL_C, _CTRL_D])
def test_a_cancelled_or_interrupted_prompt_leaves_no_record(
    monkeypatch: pytest.MonkeyPatch, method: str, keys: str
) -> None:
    choices = [PromptChoice(value="one", label="One")]
    with (
        _real_terminal(monkeypatch, keys) as backend,
        pytest.raises((EOFError, KeyboardInterrupt, ConfigError)),
    ):
        match method:
            case "confirm":
                backend.confirm("Go?", default=False)
            case "text":
                backend.text("Name", default=None)
            case "secret":
                backend.secret("Token", confirmation=False)
            case "select":
                backend.select("Pick", choices, default=None, search=False)
            case _:
                backend.multiselect("Pick", choices, defaults=[])
    assert _record(backend) == ""


def test_a_prompt_through_ui_context_ends_like_every_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for keys, error, exit_code in (
        (_CTRL_C, PromptInterruptedError, 130),
        (_CTRL_D, ConfigError, 1),
    ):
        with _real_terminal(monkeypatch, keys) as backend:
            ui = UiContext(stdin=backend.stdin, stderr=backend.stderr, prompt_backend=backend)
            with pytest.raises(error, match="prompt cancelled") as raised:
                ui.text("Name")
            assert raised.value.exit_code == exit_code


def test_the_backend_draws_every_prompt_in_its_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[ThemeSpec] = []

    def run_screen(screen: object, *, theme: ThemeSpec) -> Cancel:
        seen.append(theme)
        return Cancel()

    plain = TerminalPromptBackend(
        stdin=TtyStringIO(), stderr=TtyStringIO(), theme=BUILTIN_THEMES["plain"]
    )
    monkeypatch.setattr(plain, "run_screen", run_screen)
    choices = [PromptChoice(value="one", label="One")]
    for ask in (
        lambda: plain.text("t", default=None),
        lambda: plain.secret("s", confirmation=False),
        lambda: plain.select("p", choices, default=None, search=False),
        lambda: plain.confirm("c", default=False),
    ):
        with pytest.raises(EOFError):
            ask()
    assert seen == [BUILTIN_THEMES["plain"]] * 4

    default = TerminalPromptBackend(stdin=TtyStringIO(), stderr=TtyStringIO())
    monkeypatch.setattr(default, "run_screen", run_screen)
    with pytest.raises(EOFError):
        default.text("t", default=None)
    assert seen[-1] == BUILTIN_THEMES["default"]


_REQUEST = PickRequest(heading="Pick", catalog=PickCatalog((PickItem(id="a", label="a"),)))


def test_pick_many_returns_the_backend_result() -> None:
    picked = PickResult(title="", defaults={}, picks=(Picked(item=PickItem(id="a", label="a")),))
    backend = FakePromptBackend(pick=picked)
    ui = UiContext(stdin=TtyStringIO(), stderr=TtyStringIO(), prompt_backend=backend)
    assert ui.pick_many(_REQUEST) is picked
    assert backend.calls == ["pick_many:Pick"]


def test_pick_many_cancelled_raises_operation_cancelled() -> None:
    ui = UiContext(
        stdin=TtyStringIO(), stderr=TtyStringIO(), prompt_backend=FakePromptBackend(pick=None)
    )
    with pytest.raises(OperationCancelledError):
        ui.pick_many(_REQUEST)


def test_pick_many_rejects_duplicate_item_ids() -> None:
    duplicate = PickRequest(
        heading="Pick",
        catalog=PickCatalog((PickItem(id="a", label="a"), PickItem(id="a", label="b"))),
    )
    ui = UiContext(stdin=TtyStringIO(), stderr=TtyStringIO(), prompt_backend=FakePromptBackend())
    with pytest.raises(ConfigError, match="unique ids"):
        ui.pick_many(duplicate)


def test_pick_many_ctrl_c_interrupt_maps_to_exit_130() -> None:
    ui = UiContext(
        stdin=TtyStringIO(),
        stderr=TtyStringIO(),
        prompt_backend=FakePromptBackend(exc=KeyboardInterrupt()),
    )
    with pytest.raises(PromptInterruptedError):
        ui.pick_many(_REQUEST)


def test_terminal_backend_runs_the_real_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    with _real_terminal(monkeypatch, "\x1b[B \x13") as backend:  # down, space, ctrl-s
        picked = backend.pick_many(_REQUEST)
    assert picked is not None
    assert [p.item.id for p in picked.picks] == ["a"]


def test_terminal_backend_returns_none_when_the_picker_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _real_terminal(monkeypatch, "\x03") as backend:  # ctrl-c with nothing picked
        assert backend.pick_many(_REQUEST) is None


def test_terminal_backend_raises_keyboard_interrupt_for_an_interrupted_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = TerminalPromptBackend(stdin=TtyStringIO(), stderr=TtyStringIO())

    def run_screen(screen: object, *, theme: ThemeSpec) -> Cancel:
        return Cancel(interrupted=True)

    monkeypatch.setattr(backend, "run_screen", run_screen)
    with pytest.raises(KeyboardInterrupt):
        backend.pick_many(_REQUEST)


def test_the_backend_draws_the_picker_in_its_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[ThemeSpec] = []
    backend = TerminalPromptBackend(
        stdin=TtyStringIO(), stderr=TtyStringIO(), theme=BUILTIN_THEMES["plain"]
    )

    def run_screen(screen: object, *, theme: ThemeSpec) -> Cancel:
        seen.append(theme)
        return Cancel()

    monkeypatch.setattr(backend, "run_screen", run_screen)
    assert backend.pick_many(_REQUEST) is None
    assert seen == [BUILTIN_THEMES["plain"]]

    default_backend = TerminalPromptBackend(stdin=TtyStringIO(), stderr=TtyStringIO())
    monkeypatch.setattr(default_backend, "run_screen", run_screen)
    default_backend.pick_many(_REQUEST)
    assert seen[-1] == BUILTIN_THEMES["default"]


def test_scripted_backend_answers_pick_many() -> None:
    from untaped.testing import ScriptedPromptBackend

    picked = PickResult(title="t", defaults={}, picks=())
    backend = ScriptedPromptBackend(picks=[picked])
    assert backend.pick_many(_REQUEST) is picked
    with pytest.raises(ConfigError, match="no scripted pick_many answer"):
        backend.pick_many(_REQUEST)


def test_interrupt_escapes_a_config_error_handler() -> None:
    from untaped.testing import ScriptedPromptBackend, TtyStringIO
    from untaped.ui import UiContext

    ui = UiContext(stdin=TtyStringIO(), prompt_backend=ScriptedPromptBackend(interrupt=True))
    with pytest.raises(PromptInterruptedError):
        try:
            ui.confirm("Continue?")
        except ConfigError:  # pragma: no cover - must not catch the interrupt
            pytest.fail("Ctrl-C was swallowed by a ConfigError handler")
