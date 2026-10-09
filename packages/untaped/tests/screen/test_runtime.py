"""The runtime: dispatch order, commands, quitting and the footer, without a terminal."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

from screen.support import FakeHost, ImmediateHost, Model, Probe, logged
from untaped.screen.core import (
    SHARED_KEYS,
    Activate,
    Back,
    Binding,
    Cancel,
    Cmd,
    CmdError,
    Help,
    Interrupt,
    Key,
    NextField,
    Paste,
    PrevField,
    Quit,
    Resize,
    Submit,
)
from untaped.screen.runtime import CmdKind, Runtime, SyncHost
from untaped.testing.screens import rendered_text
from untaped.theme import BUILTIN_THEMES

SIZE = (60, 12)


@dataclass(frozen=True)
class Got:
    value: object


def _runtime(
    probe: Probe, host: FakeHost | None = None, *, started: bool = True
) -> tuple[Runtime[Model, str], FakeHost]:
    host = host or FakeHost()
    runtime = Runtime(probe.screen, host, theme=BUILTIN_THEMES["default"], size=SIZE)
    if started:
        runtime.start()
    return runtime, host


def _frame(runtime: Runtime[Model, str]) -> str:
    return rendered_text(runtime.renderable(), *runtime.size)


def _cmd(kind: CmdKind, fn: Callable[[], object]) -> Cmd:
    return Cmd(fn, write=kind == "write", suspend=kind == "suspend")


def test_messages_are_applied_in_order() -> None:
    probe = Probe(lambda model, message: logged(model, message))
    runtime, _host = _runtime(probe)
    for message in (Paste("a"), Paste("b"), Paste("c")):
        runtime.send(message)
    assert runtime.model.log == (Paste("a"), Paste("b"), Paste("c"))


def test_an_inline_message_is_delivered_in_the_same_turn_before_the_next() -> None:
    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Paste("a"):
            return logged(model, message, Cmd.send(Paste("a-follow-up")))
        return logged(model, message)

    runtime, host = _runtime(Probe(handler))
    runtime.send(Paste("a"))
    runtime.send(Paste("b"))
    assert runtime.model.log == (Paste("a"), Paste("a-follow-up"), Paste("b"))
    assert host.spawned == []  # inline: no thread


def test_an_inline_message_of_none_delivers_nothing() -> None:
    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        return logged(model, message, Cmd.send(None)) if message == Paste("a") else None

    runtime, _host = _runtime(Probe(handler))
    runtime.send(Paste("a"))
    assert runtime.model.log == (Paste("a"),)


def test_a_host_that_completes_commands_at_once_keeps_the_order() -> None:
    """A result posted from inside ``update`` is delivered after the message being handled."""

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Paste("go"):
            return logged(model, message, Cmd(lambda: Got("bg")), _cmd("write", lambda: Got("w")))
        return logged(model, message)

    runtime, host = _runtime(Probe(handler), ImmediateHost())
    runtime.send(Paste("go"))
    assert runtime.model.log == (Paste("go"), Got("bg"), Got("w"))
    assert host.spawned == ["background", "write"]
    runtime.send(Quit("done"))
    assert host.finishes == 1


def test_a_chain_of_commands_works_on_a_host_that_completes_at_once() -> None:
    """A completion that issues a command nests another completion, each in its own context."""

    class FirstDeferred(ImmediateHost):
        first: Callable[[], None] | None = None

        def spawn(self, job: Callable[[], None], *, kind: CmdKind) -> None:
            if self.first is None and not self.spawned:
                self.spawned.append(kind)
                self.first = job
            else:
                super().spawn(job, kind=kind)

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Paste("go"):
            return logged(model, message, Cmd(lambda: Got(1)))
        if message == Got(1):
            return logged(model, message, Cmd(lambda: Got(2)))
        return logged(model, message)

    host = FirstDeferred()
    runtime, _host = _runtime(Probe(handler), host)
    runtime.send(Paste("go"))
    assert host.first is not None
    host.first()  # the first completion arrives on the loop; the next ones nest inside it
    assert runtime.model.log == (Paste("go"), Got(1), Got(2))


def test_a_context_variable_set_by_update_does_not_outlive_its_message() -> None:
    var: contextvars.ContextVar[str] = contextvars.ContextVar("leak", default="unset")

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Paste("go"):
            return logged(model, message, Cmd(lambda: Got(1)), Cmd(lambda: Got(2)))
        if message == Got(1):
            var.set("set")
            return logged(model, message, Cmd(lambda: Got(("cmd", var.get()))))
        if message == Got(2):
            return logged(model, Got(("update", var.get())), Cmd(lambda: Got(("cmd2", var.get()))))
        return logged(model, message)

    runtime, host = _runtime(Probe(handler))
    runtime.send(Paste("go"))
    host.deliver_all()  # each completion is handled on its own
    assert Got(("update", "unset")) in runtime.model.log
    assert Got(("cmd", "set")) in runtime.model.log  # the command issued with the var set
    assert Got(("cmd2", "unset")) in runtime.model.log  # a command issued by the next completion


def test_a_message_sent_while_one_is_handled_waits_its_turn() -> None:
    holder: list[Runtime[Model, str]] = []

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Paste("first"):
            # A command that sends at once (as an immediate host would) must not jump the queue.
            return logged(model, message, Cmd(lambda: holder[0].send(Paste("sent")) or None))
        return logged(model, message)

    runtime, _host = _runtime(Probe(handler), ImmediateHost())
    holder.append(runtime)
    runtime.send(Paste("first"))
    assert runtime.model.log == (Paste("first"), Paste("sent"))


def test_init_commands_are_issued_by_start() -> None:
    probe = Probe(lambda model, message: logged(model, message), init=[Cmd(lambda: Got(1))])
    runtime, host = _runtime(probe)
    host.deliver_all()
    assert runtime.model.log == (Got(1),)


def test_command_result_is_delivered() -> None:
    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("r"):
            return model, [Cmd(lambda: Got("loaded"))]
        return logged(model, message)

    runtime, host = _runtime(Probe(handler))
    runtime.send(Key("r"))
    assert runtime.model.log == ()
    host.deliver_all()
    assert runtime.model.log == (Got("loaded"),)
    assert host.spawned == ["background"]


def test_a_command_returning_none_delivers_nothing() -> None:
    probe = Probe(lambda model, message: logged(model, message), init=[Cmd(lambda: None)])
    runtime, host = _runtime(probe)
    host.deliver_all()
    assert runtime.model.log == ()


@pytest.mark.parametrize("kind", ["background", "write", "suspend"])
def test_raising_command_becomes_a_cmd_error(kind: CmdKind) -> None:
    boom = RuntimeError("boom")

    def fail() -> object:
        raise boom

    probe = Probe(lambda model, message: logged(model, message), init=[_cmd(kind, fail)])
    runtime, host = _runtime(probe)
    host.deliver_all()
    assert runtime.model.log == (CmdError(boom),)
    assert host.spawned == [kind]


def test_a_failed_write_does_not_stall_the_next_write() -> None:
    def fail() -> object:
        raise ValueError("first")

    probe = Probe(
        lambda model, message: logged(model, message),
        init=[_cmd("write", fail), _cmd("write", lambda: Got("second"))],
    )
    runtime, host = _runtime(probe)
    host.deliver_all()
    assert [type(message) for message in runtime.model.log] == [CmdError, Got]


def test_quit_abandons_a_background_command() -> None:
    release = threading.Event()
    started = threading.Event()

    def slow() -> object:
        started.set()
        release.wait(5)
        return Got("late")

    probe = Probe(lambda model, message: logged(model, message), init=[Cmd(slow)])
    runtime, host = _runtime(probe, FakeHost(threads=True))
    assert started.wait(5)
    runtime.send(Quit("done"))
    assert host.finishes == 1  # finished while the command still runs
    assert not runtime.saving
    release.set()
    host.deliver()
    assert runtime.model.log == ()  # the late result was dropped
    assert host.finishes == 1
    host.join()


def test_quit_waits_for_a_write_command() -> None:
    release = threading.Event()
    started = threading.Event()
    written: list[str] = []

    def write() -> object:
        started.set()
        release.wait(5)
        written.append("done")
        return Got("written")

    probe = Probe(lambda model, message: logged(model, message), init=[_cmd("write", write)])
    runtime, host = _runtime(probe, FakeHost(threads=True))
    assert started.wait(5)
    runtime.send(Quit("done"))
    assert host.finishes == 0
    assert runtime.saving
    assert "saving…" in _frame(runtime)
    release.set()
    host.deliver()
    assert (host.finishes, runtime.saving, written) == (1, False, ["done"])
    assert runtime.model.log == ()  # a message from a write after the outcome is dropped
    assert isinstance(runtime.outcome, Quit)
    host.join()


def test_quit_waits_for_queued_writes_too() -> None:
    order: list[str] = []
    gate = threading.Event()

    def first() -> object:
        gate.wait(5)
        order.append("first")
        return None

    def second() -> object:
        order.append("second")
        return None

    probe = Probe(init=[_cmd("write", first), _cmd("write", second)])
    runtime, host = _runtime(probe, FakeHost(threads=True))
    runtime.send(Cancel())
    assert host.finishes == 0
    gate.set()
    host.deliver(2)
    assert order == ["first", "second"]
    assert host.finishes == 1
    host.join()


def test_a_write_issued_in_the_same_turn_as_quit_still_runs() -> None:
    ran: list[str] = []

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("s"):
            return model, [
                Cmd.send(Quit("saved")),
                _cmd("write", lambda: ran.append("write")),
            ]
        return None

    runtime, host = _runtime(Probe(handler))
    runtime.send(Key("s"))
    assert host.finishes == 0
    host.deliver_all()
    assert (ran, host.finishes) == (["write"], 1)


def test_writes_run_one_at_a_time_in_issue_order() -> None:
    gates = [threading.Event() for _ in range(3)]
    order: list[str] = []

    def write(index: int) -> Callable[[], object]:
        def run() -> object:
            order.append(f"start {index}")
            gates[index].wait(5)
            order.append(f"end {index}")
            return Got(index)

        return run

    cmds = [_cmd("write", write(index)) for index in range(3)]
    probe = Probe(lambda model, message: logged(model, message), init=cmds)
    runtime, host = _runtime(probe, FakeHost(threads=True))
    assert host.spawned == ["write"]  # the others wait their turn
    for index in range(3):
        gates[index].set()
        host.deliver()
        assert host.spawned == ["write"] * min(index + 2, 3)
    assert order == ["start 0", "end 0", "start 1", "end 1", "start 2", "end 2"]
    assert runtime.model.log == (Got(0), Got(1), Got(2))
    host.join()


def test_a_second_background_command_runs_concurrently() -> None:
    both_running = threading.Barrier(2, timeout=5)

    def meet() -> object:
        both_running.wait()
        return Got("met")

    probe = Probe(lambda model, message: logged(model, message), init=[Cmd(meet), Cmd(meet)])
    runtime, host = _runtime(probe, FakeHost(threads=True))
    assert host.spawned == ["background", "background"]
    host.deliver(2)  # neither could finish before the other started
    assert runtime.model.log == (Got("met"), Got("met"))
    host.join()


def test_suspend_commands_are_serialized_and_get_kind_suspend() -> None:
    gate = threading.Event()
    order: list[str] = []

    def first() -> object:
        gate.wait(5)
        order.append("first")
        return Got(1)

    def second() -> object:
        order.append("second")
        return Got(2)

    probe = Probe(
        lambda model, message: logged(model, message),
        init=[_cmd("suspend", first), _cmd("suspend", second)],
    )
    runtime, host = _runtime(probe, FakeHost(threads=True))
    assert host.spawned == ["suspend"]
    gate.set()
    host.deliver(2)
    assert host.spawned == ["suspend", "suspend"]
    assert order == ["first", "second"]
    assert runtime.model.log == (Got(1), Got(2))
    host.join()


def test_a_queued_suspend_command_is_dropped_when_the_screen_quits() -> None:
    gate = threading.Event()
    ran: list[str] = []

    def first() -> object:
        gate.wait(5)
        return None

    probe = Probe(init=[_cmd("suspend", first), _cmd("suspend", lambda: ran.append("second"))])
    runtime, host = _runtime(probe, FakeHost(threads=True))
    runtime.send(Cancel())
    gate.set()
    host.deliver()
    assert (ran, host.spawned) == ([], ["suspend"])
    host.join()


_VAR: contextvars.ContextVar[str] = contextvars.ContextVar("screen_test_var", default="unset")


@pytest.mark.parametrize("threaded", [False, True], ids=["same-thread", "worker-thread"])
@pytest.mark.parametrize("kind", ["background", "write", "suspend"])
def test_commands_see_context_vars_set_around_run(kind: CmdKind, threaded: bool) -> None:
    token = _VAR.set("around-run")
    try:
        probe = Probe(
            lambda model, message: logged(model, message),
            init=[_cmd(kind, lambda: Got(_VAR.get()))],
        )
        runtime, host = _runtime(probe, FakeHost(threads=threaded))
    finally:
        _VAR.reset(token)
    # The caller moved on before the command ran; it still sees the value it was issued under.
    moved_on = _VAR.set("after-issue")
    try:
        if threaded:
            host.deliver()
        else:
            host.deliver_all()
    finally:
        _VAR.reset(moved_on)
    assert runtime.model.log == (Got("around-run"),)
    host.join()


def test_a_context_var_set_after_a_command_was_issued_is_not_visible() -> None:
    probe = Probe(
        lambda model, message: logged(model, message), init=[Cmd(lambda: Got(_VAR.get()))]
    )
    runtime, host = _runtime(probe)
    token = _VAR.set("too-late")
    try:
        host.deliver_all()
    finally:
        _VAR.reset(token)
    assert runtime.model.log == (Got("unset"),)


@pytest.mark.parametrize("threaded", [False, True], ids=["same-thread", "worker-thread"])
@pytest.mark.parametrize("kind", ["background", "write", "suspend"])
def test_a_command_issued_by_an_update_keeps_the_callers_context(
    kind: CmdKind, threaded: bool
) -> None:
    """The host delivers a completion in the poster's context; the chain must not lose ours."""

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Got("first"):
            seen = Got(("update saw", _VAR.get()))
            return logged(model, seen, _cmd(kind, lambda: Got(("second saw", _VAR.get()))))
        return logged(model, message)

    token = _VAR.set("around-run")
    try:
        probe = Probe(handler, init=[_cmd(kind, lambda: Got("first"))])
        runtime, host = _runtime(probe, FakeHost(threads=threaded))
    finally:
        _VAR.reset(token)
    moved_on = _VAR.set("after-issue")
    try:
        if threaded:
            host.deliver(2)
        else:
            host.deliver_all()
    finally:
        _VAR.reset(moved_on)
    assert runtime.model.log == (
        Got(("update saw", "around-run")),
        Got(("second saw", "around-run")),
    )
    assert host.spawned == [kind, kind]
    host.join()


def test_a_chain_of_commands_keeps_the_context_on_a_real_event_loop() -> None:
    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Got("first"):
            return logged(model, message, Cmd(lambda: Got(_VAR.get())))
        if message == Got("around-loop"):
            return logged(model, message, Cmd.send(Quit("done")))
        return logged(model, message)

    class LoopHost(FakeHost):
        def __init__(self, loop: asyncio.AbstractEventLoop, done: asyncio.Future[None]) -> None:
            super().__init__(threads=True)
            self._loop = loop
            self._done = done

        def post(self, call: Callable[[], None]) -> None:
            self._loop.call_soon_threadsafe(call)

        def finish(self) -> None:
            super().finish()
            self._done.set_result(None)

    async def main() -> tuple[Runtime[Model, str], LoopHost]:
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        host = LoopHost(loop, done)
        _VAR.set("around-loop")
        runtime = Runtime(
            Probe(handler, init=[Cmd(lambda: Got("first"))]).screen,
            host,
            theme=BUILTIN_THEMES["default"],
            size=SIZE,
        )
        runtime.start()
        await asyncio.wait_for(done, 5)
        return runtime, host

    runtime, host = asyncio.run(main())
    host.join()
    assert runtime.model.log == (Got("first"), Got("around-loop"))
    assert runtime.outcome == Quit("done")


@pytest.mark.parametrize("kind", ["write", "suspend"])
def test_commands_start_in_the_order_they_were_issued(kind: CmdKind) -> None:
    probe = Probe(init=[_cmd(kind, lambda: None), Cmd(lambda: None), _cmd(kind, lambda: None)])
    _runtime(probe, host := FakeHost())
    assert host.spawned == [kind, "background"]  # the second one of its kind waits its turn


def test_update_runs_on_the_loop_thread_only() -> None:
    probe = Probe(
        lambda model, message: logged(model, message),
        init=[Cmd(lambda: Got(1)), _cmd("write", lambda: Got(2)), _cmd("suspend", lambda: Got(3))],
    )
    runtime, host = _runtime(probe, FakeHost(threads=True))
    host.deliver(3)
    runtime.send(Key("x"))
    host.join()
    assert set(probe.threads) == {threading.current_thread().name}


def test_resize_is_stored_and_delivered() -> None:
    probe = Probe(lambda model, message: logged(model, message))
    runtime, _host = _runtime(probe)
    runtime.send(Resize(40, 8))
    assert runtime.size == (40, 8)
    assert runtime.model.log == (Resize(40, 8),)
    assert len(_frame(runtime).splitlines()) == 8


def test_quit_result_is_the_outcome() -> None:
    runtime, host = _runtime(Probe())
    runtime.send(Quit("the result"))
    assert runtime.outcome == Quit("the result")
    assert host.finishes == 1


def test_cancel_and_quit_never_reach_update() -> None:
    probe = Probe(lambda model, message: logged(model, message))
    runtime, _host = _runtime(probe)
    runtime.send(Cancel())
    runtime.send(Quit("x"))
    assert probe.seen == []


def test_first_outcome_wins() -> None:
    runtime, host = _runtime(Probe())
    runtime.send(Quit("first"))
    runtime.send(Cancel())
    runtime.send(Quit("second"))
    assert runtime.outcome == Quit("first")
    assert host.finishes == 1


def test_messages_after_the_outcome_are_dropped_except_resize() -> None:
    probe = Probe(lambda model, message: logged(model, message))
    runtime, _host = _runtime(probe, FakeHost(threads=True))
    runtime.send(Cancel())
    runtime.send(Key("x"))
    runtime.send(Resize(10, 5))
    assert probe.seen == []
    assert runtime.size == (10, 5)


def test_unhandled_back_cancels() -> None:
    probe = Probe()
    runtime, host = _runtime(probe)
    runtime.send(Key("esc"))
    assert probe.seen == [Key("esc"), Back()]
    assert runtime.outcome == Cancel(interrupted=False)
    assert host.finishes == 1


def test_unhandled_interrupt_cancels_interrupted() -> None:
    probe = Probe()
    runtime, _host = _runtime(probe)
    runtime.send(Key("ctrl-c"))
    assert probe.seen == [Key("ctrl-c"), Interrupt()]
    assert runtime.outcome == Cancel(interrupted=True)


def test_a_handled_back_does_not_cancel() -> None:
    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if isinstance(message, Back):
            return replace(model, flag=True), []
        return None

    runtime, host = _runtime(Probe(handler))
    runtime.send(Key("esc"))
    assert runtime.model.flag
    assert runtime.outcome is None
    assert host.finishes == 0


def test_a_back_answered_with_a_command_does_not_cancel() -> None:
    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        return (model, [Cmd.send(Paste("closed"))]) if isinstance(message, Back) else None

    runtime, _host = _runtime(Probe(handler))
    runtime.send(Key("esc"))
    assert runtime.outcome is None


def test_dispatch_order_is_component_then_binding_then_shared() -> None:
    @dataclass(frozen=True)
    class BindingMessage:
        key: str

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        # "x" is consumed by the focused component, which also takes "enter" before Activate.
        if message in (Key("x"), Key("enter"), BindingMessage("x"), BindingMessage("y"), Submit()):
            return logged(model, message)
        return None

    keys = (
        Binding("x", "bound x", BindingMessage("x")),
        Binding("y", "bound y", BindingMessage("y")),
    )
    probe = Probe(handler, keys=keys)
    runtime, _host = _runtime(probe)
    runtime.send(Key("x"))  # component handled it: the binding never fires
    runtime.send(Key("y"))  # unhandled by the component: the binding fires
    runtime.send(Key("enter"))  # the component took the key: no Activate
    runtime.send(Key("ctrl-s"))  # nothing before it took ctrl-s: Submit
    assert runtime.model.log == (Key("x"), BindingMessage("y"), Key("enter"), Submit())
    assert probe.seen == [
        Key("x"),
        Key("y"),
        BindingMessage("y"),
        Key("enter"),
        Key("ctrl-s"),
        Submit(),
    ]


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("esc", Back()),
        ("ctrl-c", Interrupt()),
        ("tab", NextField()),
        ("shift-tab", PrevField()),
        ("enter", Activate()),
        ("ctrl-s", Submit()),
    ],
)
def test_each_shared_key_sends_its_message_after_the_component_passed(
    key: str, message: object
) -> None:
    probe = Probe()
    runtime, _host = _runtime(probe)
    runtime.send(Key(key))
    assert probe.seen[:2] == [Key(key), message]


def test_an_unhandled_tab_or_enter_is_ignored() -> None:
    runtime, host = _runtime(Probe())
    for key in ("tab", "shift-tab", "enter", "ctrl-s", "up", "z"):
        runtime.send(Key(key))
    assert runtime.outcome is None
    assert host.finishes == 0


def test_a_binding_with_a_false_when_is_inactive_and_not_in_the_footer() -> None:
    keys = (Binding("ctrl-r", "refresh", Paste("refresh"), when=lambda model: model.flag),)

    def handler(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        return (replace(model, flag=True), []) if message == Key("f") else None

    probe = Probe(handler, keys=keys)
    runtime, _host = _runtime(probe)
    assert "refresh" not in _frame(runtime)
    runtime.send(Key("ctrl-r"))
    assert Paste("refresh") not in probe.seen
    runtime.send(Key("f"))  # the model now makes the binding active
    assert "ctrl-r refresh" in _frame(runtime)
    runtime.send(Key("ctrl-r"))
    assert Paste("refresh") in probe.seen


def test_a_footer_only_binding_is_not_dispatched() -> None:
    probe = Probe(keys=(Binding("up", "move", None),))
    runtime, _host = _runtime(probe)
    runtime.send(Key("up"))
    assert probe.seen == [Key("up")]
    assert "up move" in _frame(runtime)


def test_question_mark_opens_help_only_when_unhandled() -> None:
    def typing(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        return logged(model, message) if message == Key("?") else None

    typed, _host = _runtime(Probe(typing))
    typed.send(Key("?"))
    assert not typed.help_open
    assert typed.model.log == (Key("?"),)

    free, _host = _runtime(Probe())
    free.send(Key("?"))
    assert free.help_open


def test_help_overlay_lists_every_active_binding_and_shared_key() -> None:
    keys = (
        Binding("ctrl-r", "refresh", Paste("r")),
        Binding("up", "move", None),
        Binding("ctrl-u", "hidden", Paste("u"), when=lambda model: False),
    )
    runtime, _host = _runtime(Probe(keys=keys))
    runtime.send(Key("?"))
    frame = _frame(runtime)
    for key, label in (("ctrl-r", "refresh"), ("up", "move")):
        assert any(key in line and label in line for line in frame.splitlines())
    for shared in SHARED_KEYS.values():
        assert any(shared.key in line and shared.label in line for line in frame.splitlines())
    assert "hidden" not in frame


@pytest.mark.parametrize("closer", ["?", "esc", "enter"])
def test_help_overlay_closes_and_swallows_other_keys(closer: str) -> None:
    probe = Probe()
    runtime, _host = _runtime(probe)
    runtime.send(Key("?"))
    runtime.send(Key("x"))
    runtime.send(Paste("pasted"))
    assert probe.seen == [Key("?"), Help()]
    runtime.send(Key(closer))
    assert not runtime.help_open
    assert runtime.outcome is None
    assert "Keys" not in _frame(runtime)


def test_ctrl_c_in_the_help_overlay_cancels_interrupted() -> None:
    runtime, _host = _runtime(Probe())
    runtime.send(Key("?"))
    runtime.send(Key("ctrl-c"))
    assert runtime.outcome == Cancel(interrupted=True)


def test_footer_reads_saving_while_waiting() -> None:
    gate = threading.Event()
    probe = Probe(init=[_cmd("write", lambda: gate.wait(5) and None)])
    runtime, host = _runtime(probe, FakeHost(threads=True))
    assert "saving" not in _frame(runtime)
    runtime.send(Cancel())
    last = _frame(runtime).splitlines()[-1]
    assert last == "saving…"
    gate.set()
    host.deliver()
    host.join()
    assert not runtime.saving


def test_the_footer_uses_the_themes_ellipsis_and_separator() -> None:
    gate = threading.Event()
    probe = Probe(init=[_cmd("write", lambda: gate.wait(5) and None)])
    host = FakeHost(threads=True)
    runtime = Runtime(probe.screen, host, theme=BUILTIN_THEMES["plain"], size=SIZE)
    runtime.start()
    runtime.send(Cancel())
    assert _frame(runtime).splitlines()[-1] == "saving..."
    gate.set()
    host.deliver()
    host.join()


def test_sync_host_runs_jobs_then_posted_calls_until_both_are_empty() -> None:
    host = SyncHost()
    order: list[str] = []

    def job() -> None:
        order.append("job")
        host.post(lambda: order.append("posted"))

    host.spawn(job, kind="background")
    host.spawn(job, kind="write")
    host.pump()
    assert order == ["job", "job", "posted", "posted"]
    host.pump()  # idle: nothing happens
    assert order == ["job", "job", "posted", "posted"]
    host.finish()
    assert host.finished
