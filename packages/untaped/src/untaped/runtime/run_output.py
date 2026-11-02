"""A run's output: its ``diag`` handle, the failures it counts, and the current sink.

Each run's ``diag`` is its :class:`RunOutput`: its sink
(:class:`~untaped.runtime.sink.RunSink`) and the most severe failure it
counted, following the run's :class:`~untaped.sdk._run.OutputPolicy`
(``Run.policy``). A child run for a nested pick shares its parent's; a
nested invocation has its own. :func:`current_sink` is the current run's
sink; a line emitted off every run (before the root opens one, or from a
thread that copied no context) goes to a default text sink and counts
nothing.

Emitting a :class:`~untaped.sdk.diagnostic.Failure` (a subclass too)
counts its ``exit_code`` toward the run's exit code
(:func:`failure_exit_code`); a ``Stale`` whose ``error`` is set is a
warning and counts nothing. While a run is open (:func:`opened`), a Python
warning that is shown reaches the sink like any line.
"""

from __future__ import annotations

import threading
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, TextIO, cast

from pydantic import BaseModel

from untaped.runtime.render import render_styled
from untaped.runtime.sink import HINT_PREFIX, RunSink, Sink, default_policy
from untaped.sdk._run import OutputPolicy, run_or_none
from untaped.sdk.diagnostic import (
    NOT_COUNTED,
    Deprecation,
    Diagnostic,
    Failure,
    Message,
    format_error,
    kind_of,
)
from untaped.sdk.errors import ErrorCategory, ExitCode, UntapedError, combine_exit_codes
from untaped.sdk.progress import ProgressHandle
from untaped.sdk.records import OutcomeRecord

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.sdk._run import Run

#: Where a line off every run goes: before the root opens one, or from a bare thread.
_DEFAULT = RunSink(None)


def _output() -> RunOutput | None:
    """The current run's output (``None``: off every run)."""
    run = run_or_none()
    return run.diag if run is not None and isinstance(run.diag, RunOutput) else None


def current_sink() -> Sink:
    """The current run's sink; off every run, the default text sink; never ``None``."""
    output = _output()
    return output.sink if output is not None else _DEFAULT


@contextmanager
def opened(output: RunOutput) -> Iterator[None]:
    """Open a run's ``output`` for the block its run is current in; print its held lines after.

    For its duration a Python warning that is shown (not one a filter turns
    into an error or ignores) reaches the sink like any line: an
    :class:`~untaped.sdk.marks.UntapedDeprecationWarning` as the
    ``untaped.deprecation`` record it carries, any other as a warning
    message, so JSON diagnostics stay one object per line. A warning
    something else records (``warnings.catch_warnings(record=True)``,
    ``pytest.warns``) is left to it.

    Python hides a ``DeprecationWarning`` raised outside ``__main__``, so
    for the run untaped shows its own (:func:`_shown_deprecations`), each
    once, unless a filter someone set decides; the filters are restored after.
    """
    with warnings.catch_warnings():
        _shown_deprecations()
        shown = warnings.showwarning
        warnings.showwarning = _WarningRoute(shown)
        try:
            yield
        finally:
            warnings.showwarning = shown
            output.sink.close()


#: The filters Python installs at startup that cover a ``DeprecationWarning``, in their order
#: (as ``(action, module)``; a module of ``None`` covers every module).
_PYTHON_DEFAULTS = (("default", "__main__"), ("ignore", None))


def _shown_deprecations() -> None:
    """Show :class:`~untaped.sdk.marks.UntapedDeprecationWarning` for the run.

    Only while the filters covering it are Python's startup defaults alone:
    any other (a ``-W`` flag, ``PYTHONWARNINGS``, a test harness's or the
    program's own, even one spelled like a default) decides instead.
    """
    from untaped.sdk.marks import UntapedDeprecationWarning  # noqa: PLC0415 - avoids a cycle

    current = getattr(warnings, "_get_filters", lambda: warnings.filters)()
    covering = tuple(
        (action, getattr(module, "pattern", module))
        if message is None and lineno == 0 and category is DeprecationWarning
        else None
        for action, message, category, module, lineno in current
        if issubclass(UntapedDeprecationWarning, category)
    )
    if covering == _PYTHON_DEFAULTS:
        warnings.filterwarnings("default", category=UntapedDeprecationWarning)


#: How Python shows a warning when nothing records it; ``catch_warnings(record=True)``
#: swaps it for its log while it records.
_SHOW_UNRECORDED = getattr(warnings, "_showwarnmsg_impl", None)


def _recording() -> bool:
    """Whether a ``catch_warnings(record=True)`` is recording the warnings shown now.

    It swaps the show function, or with context-aware warnings (``-X
    context_aware_warnings``, the default on free-threaded builds) sets the
    current context's ``log``.
    """
    if getattr(warnings, "_showwarnmsg_impl", None) is not _SHOW_UNRECORDED:
        return True
    context = getattr(warnings, "_get_context", None)
    return context is not None and getattr(context(), "log", None) is not None


class _WarningRoute:
    """``warnings.showwarning`` while a run is open: a warning nothing records goes to the sink."""

    def __init__(self, shown: Callable[..., None]) -> None:
        self._shown = shown

    def __call__(
        self,
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: TextIO | None = None,
        line: str | None = None,
    ) -> None:
        if _recording():
            self._shown(message, category, filename, lineno, file, line)
            return
        _show_warning(message, category, filename, lineno)


def _show_warning(
    message: Warning | str, category: type[Warning], filename: str, lineno: int
) -> None:
    """A shown Python warning as a diagnostic on the sink."""
    from untaped.sdk.marks import (  # noqa: PLC0415 - avoids a cycle
        UntapedDeprecationWarning,
        first_notice,
    )

    if isinstance(message, UntapedDeprecationWarning) and message.id is not None:
        if not first_notice(message.id):
            return
        current_sink().emit(
            Deprecation(
                message=str(message),
                id=message.id,
                removed_in=message.removed_in,
                distribution=message.distribution,
                replacement=message.replacement,
            )
        )
        return
    text = f"{category.__name__}: {message} ({filename}:{lineno})"
    current_sink().emit(Message(level="warning", message=text))


def count_row_failures(rows: Iterable[object]) -> None:
    """Count each failed row toward the invocation's exit code, as emitting it does.

    A model row counts every top-level field holding a
    :class:`~untaped.sdk.diagnostic.Failure` (its ``error``, or a suite's
    ``failure``) except one marked
    :data:`~untaped.sdk.diagnostic.NOT_COUNTED` (a remembered failure); a
    mapping row counts its ``error`` mapping by its ``exit_code`` or
    ``category``. A row marked failed with no error is not counted.
    """
    output = _output()
    if output is None:
        return
    for row in rows:
        if isinstance(row, BaseModel):
            for name, info in type(row).model_fields.items():
                if NOT_COUNTED in info.metadata:
                    continue
                value = getattr(row, name, None)
                if isinstance(value, Failure):
                    output.note(value.exit_code)
        elif isinstance(row, Mapping):
            code = _row_error_code(row.get("error"))
            if code is not None:
                output.note(code)


def count_failed_actions(rows: Iterable[object]) -> None:
    """Count each outcome row whose ``action`` says it failed, error or not.

    ``failed``, ``partial`` and ``conflict`` count ``1`` (a row's error
    counts on its own, through :func:`count_row_failures`), unless a field
    marked :data:`~untaped.sdk.diagnostic.NOT_COUNTED` holds a
    :class:`~untaped.sdk.diagnostic.Failure`: the failure is remembered from
    an earlier run. A command's printed rows go through it.
    """
    output = _output()
    if output is None:
        return
    for row in rows:
        if not isinstance(row, OutcomeRecord) or not row.failed:
            continue
        remembered = any(
            NOT_COUNTED in info.metadata and isinstance(getattr(row, name, None), Failure)
            for name, info in type(row).model_fields.items()
        )
        if not remembered:
            output.note(ExitCode.FAILURE)


def _row_error_code(error: object) -> int | None:
    """The exit code a dumped ``Failure`` mapping stands for (``None``: not one)."""
    if not isinstance(error, Mapping):
        return None
    code = error.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    try:
        return int(ErrorCategory(str(error.get("category"))).exit_code)
    except ValueError:
        return None


def failure_exit_code(*codes: int) -> int:
    """The exit code of a failed run: the most severe of ``codes`` and every counted failure.

    Never below ``1``: a failed run whose failures carry no category exits ``1``.
    """
    output = _output()
    code = combine_exit_codes(*codes, output.worst if output is not None else ExitCode.OK)
    return code if code not in (ExitCode.OK, ExitCode.PREDICATE) else ExitCode.FAILURE


def exit_on_counted_failure() -> None:
    """Exit with the worst code the invocation counted, if it counted a failure.

    A command that emitted a failure and returned normally still fails:
    the exit code is derived from what was emitted.
    """
    output = _output()
    if output is not None and output.worst != ExitCode.OK:
        raise SystemExit(output.worst)


def derived_exit_code(code: str | int | None) -> str | int | None:
    """The code a run exiting with ``code`` ends with, given the failures it counted.

    The most severe of the two wins (a predicate hit, ``3``, never hides a
    counted failure); a code that is not a number is left alone.
    """
    output = _output()
    if output is None or output.worst == ExitCode.OK:
        return code
    if code is None:
        return output.worst
    if isinstance(code, str):
        return code
    return combine_exit_codes(code, output.worst)


def failure_text(error: UntapedError | Failure, *, item: str | None = None) -> str:
    """A failure's text line: ``error: [<item>: ]<message>``, then its ``hint:`` line."""
    prefix = "" if item is None else f"{item}: "
    if isinstance(error, UntapedError):
        body = format_error(error)
    else:
        body = (
            error.message if error.hint is None else f"{error.message}\n{HINT_PREFIX}{error.hint}"
        )
    return f"error: {prefix}{body}"


class Diagnostics:
    """The stderr handle: one method per level.

    Every call resolves the current run's sink when it is made, never when
    the handle is built, so a handle kept in a module constant still writes
    to the invocation that uses it. ``--quiet`` mutes ``info``, ``success``
    and progress; a failure counts toward the exit code. A command's
    ``ctx.diag`` is its run's own :class:`RunOutput`.
    """

    def _sink(self) -> Sink:
        return current_sink()

    def emit(self, diagnostic: Diagnostic) -> None:
        """Emit any composed kind, core's or a plugin's own (listed in its ``records``)."""
        self._sink().emit(diagnostic)

    def info(
        self,
        text: str | Text | Sequence[BaseModel],
        *,
        hint: str | None = None,
        unmuted: bool = False,
    ) -> None:
        """An ``info`` line; a Rich ``Text`` renders styled on a terminal, plain elsewhere.

        Records print as one table in the run's theme, as core prints stdout
        rows: their ``Column`` fields, empty columns hidden and the list view
        where the theme says so. No records print nothing.

        ``-q`` mutes commentary: lines the user did not ask for and need not
        act on. A preview before a prompt, a stream the user asked to watch,
        and output a flag exists to produce are not commentary and print
        under ``-q``. Warnings, failures and hints always print.

        Pass ``unmuted=True`` for a line that is not commentary. A blank
        ``text`` prints an empty line in text (a separator) and nothing under
        JSON.
        """
        if isinstance(text, str):
            self._sink().emit(Message(level="info", message=text, hint=hint), unmuted=unmuted)
            return
        if isinstance(text, Sequence):  # records: a Text is no sequence
            self._records(cast("Sequence[BaseModel]", text), hint=hint, unmuted=unmuted)
            return
        tail = "" if hint is None else f"\n{HINT_PREFIX}{hint}"

        def styled(color: bool) -> str:
            return render_styled(text, colorize=color) + tail

        self._sink().emit(
            Message(level="info", message=text.plain, hint=hint), rendered=styled, unmuted=unmuted
        )

    def _records(self, records: Sequence[BaseModel], *, hint: str | None, unmuted: bool) -> None:
        """``records`` as one ``info`` table: the stdout table's layout, on stderr."""
        from untaped.sdk._cli import stderr_table  # noqa: PLC0415 - tables are heavy

        if not records:
            return
        tail = "" if hint is None else f"\n{HINT_PREFIX}{hint}"
        plain = stderr_table(records, colorize=False, plain=True)  # what JSON diagnostics carry
        self._sink().emit(
            Message(level="info", message=plain, hint=hint),
            rendered=lambda color: stderr_table(records, colorize=color) + tail,
            unmuted=unmuted,
        )

    def success(self, text: str) -> None:
        """A ``success`` line, muted by ``--quiet`` like ``info``."""
        self._sink().emit(Message(level="success", message=text))

    def warning(self, text: str, *, hint: str | None = None) -> None:
        """A ``warning:`` line, never muted."""
        self._sink().emit(Message(level="warning", message=text, hint=hint))

    def hint(self, text: str) -> None:
        """A ``hint:`` line: what to do next, never muted."""
        self._sink().emit(Message(level="hint", message=text))

    def failure(self, error: UntapedError | Failure, *, item: str | None = None) -> Failure:
        """Emit ``error`` as an ``error:`` line (``item`` names a per-item one) and return it.

        It counts toward the invocation's exit code.
        """
        failure = error if isinstance(error, Failure) else Failure.from_exception(error)
        if kind_of(type(failure)) is None:
            # A kind-less subclass (a payload nested in rows) prints as a plain failure.
            failure = Failure.model_validate(
                {name: getattr(failure, name) for name in Failure.model_fields}
            )
        if item is not None:
            failure = failure.model_copy(update={"item": item})
        self._sink().emit(failure, rendered=failure_text(error, item=item))
        return failure

    def relay(self, text: str) -> None:
        """Print another tool's output (a hook's stderr, a job log) as is, never counted.

        An ``error:`` line it relays is not a failure.
        """
        self._sink().relay(text)

    def progress(self, label: str) -> AbstractContextManager[ProgressHandle]:
        """Report progress for a blocking operation: a spinner on a terminal, else lines.

        On a pipe and under JSON, ``untaped.progress`` lines start after 2 s,
        then come per phase, per 10 % and every 30 s while a step waits, and
        end with one ``done`` or ``failed`` line; ``--verbose`` prints every
        update. Pass the handle on: an inner layer reports through it.
        """
        return self._sink().progress(label)


class RunOutput(Diagnostics):
    """A run's ``diag``: its sink and the most severe failure it counted.

    ``untaped.runtime.run.start_run`` builds one per run and binds it to that
    run (:meth:`bind`), whose policy its sink follows; it writes to its own
    sink wherever it is called. A child run for a nested pick shares its
    parent's (sink, count and the parent's policy); a nested invocation is
    a child run with its own.
    """

    def __init__(self) -> None:
        self._run: Run | None = None
        self._lock = threading.Lock()
        self._worst: int = ExitCode.OK
        self.sink = RunSink(self)

    def bind(self, run: Run) -> None:
        """Make ``run`` the run whose policy the sink follows; once."""
        if self._run is not None:
            raise RuntimeError("a run's output is bound once")
        self._run = run

    @property
    def policy(self) -> OutputPolicy:
        """The policy of the run this output is bound to (unbound: the default sink's)."""
        return self._run.policy if self._run is not None else default_policy()

    def note(self, code: int) -> None:
        with self._lock:
            self._worst = combine_exit_codes(self._worst, code)

    @property
    def worst(self) -> int:
        with self._lock:
            return self._worst

    def _sink(self) -> Sink:
        return self.sink
