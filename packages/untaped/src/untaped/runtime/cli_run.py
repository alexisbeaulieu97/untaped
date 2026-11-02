"""How core runs a cyclopts app: exit codes, a closed pipe, parse errors and the error boundary."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import NoReturn

from cyclopts import App, ResultAction
from cyclopts.exceptions import (
    CycloptsError,
    MissingArgumentError,
    UnknownCommandError,
    UnknownOptionError,
    UnusedCliTokensError,
)
from rich.console import Console

from untaped.runtime.default_format import note_requested_format
from untaped.runtime.run_output import (
    Diagnostics,
    derived_exit_code,
    exit_on_counted_failure,
    failure_exit_code,
    raise_usage,
)
from untaped.sdk._run import is_verbose
from untaped.sdk.diagnostic import Debug
from untaped.sdk.errors import ExitCode, Failed, Matched, OperationCancelledError, UntapedError
from untaped.sdk.words import not_found, q

#: Cyclopts' list of the commands an unknown one was looked up among.
_AVAILABLE = re.compile(r" Available commands: (?P<known>.*)$")
#: One suggested name; two synonyms (``"a" or "b"``) are not one.
_ONE_SUGGESTION = re.compile(r' Did you mean "(?P<like>[^"]+)"\?')


def parse_error(exc: CycloptsError) -> str:
    """A command line cyclopts refused, said as untaped says errors (lowercase, no period).

    A known option the parser refused is a stray token after ``--``: an
    unexpected argument, not an unknown option.
    """
    text = str(exc)
    if isinstance(exc, UnknownOptionError):
        value = exc.token.keyword or exc.token.value
        like = _suggestion(text)
        if like == value:
            return f"unexpected argument after --: {q(value)}"
        message = f"unknown option: {value}"
        return f"{message}; did you mean {like}?" if like else message
    if isinstance(exc, UnusedCliTokensError) and exc.unused_tokens:
        noun = "argument" if len(exc.unused_tokens) == 1 else "arguments"
        return f"unexpected {noun}: {', '.join(q(token) for token in exc.unused_tokens)}"
    if isinstance(exc, UnknownCommandError) and exc.unused_tokens:
        message = not_found("command", exc.unused_tokens[0])
        if like_found := _ONE_SUGGESTION.search(text):
            return f"{message}; did you mean {q(like_found['like'])}?"
        if available := _AVAILABLE.search(text):
            known = available["known"]
            known = (
                known.removesuffix(", ...") + ", …"
                if known.endswith(", ...")
                else known.removesuffix(".")
            )
            return f"{message}; known: {known}"
        return message
    if type(exc) is MissingArgumentError and exc.argument is not None:
        name = exc.keyword or exc.argument.name
        where = f"{' '.join(exc.command_chain)}: " if exc.command_chain else ""
        if exc.argument.token_count()[0] == 1 and not exc.tokens_so_far:
            return f"{where}{name} requires an argument"
    return _house(text)


def _suggestion(text: str) -> str | None:
    head, found, tail = text.rpartition(" Did you mean ")
    return tail.removesuffix("?") if found and head else None


def _house(text: str) -> str:
    """Cyclopts' sentence in the house style: lowercase first word, no closing period."""
    text = text.strip().removesuffix(".")
    if text[:1].isupper() and not text[1:2].isupper():
        text = text[0].lower() + text[1:]
    return text


def run_cyclopts_app(
    app: App,
    tokens: Iterable[str] | None,
    *,
    console: Console | None = None,
    error_console: Console | None = None,
    result_action: ResultAction | None = None,
) -> object:
    """Run a Cyclopts app while preserving untaped's usage-error contract.

    Ctrl-C anywhere exits ``130`` without a traceback. Also converts a broken
    downstream pipe — the consumer closed it early, e.g.
    ``untaped <plugin> list | head`` or a consumer that exits before reading all of
    its input — into a quiet ``SystemExit(0)`` (the standard CLI behaviour:
    the consumer chose to stop reading, which is not a failure). Without this the producer's
    buffered stdout flush fails at interpreter shutdown and Python prints a
    noisy ``Exception ignored while flushing sys.stdout: BrokenPipeError``.
    The invocation writes to the current run's output (the root starts one
    first; the testing kit runs each invocation under its own); a parse error
    follows the ``--format`` its tokens ask for. A command that emitted a
    failure and returned normally exits with the worst code it counted.
    While it runs, :func:`given_tokens` hands ``app``'s command the tokens
    as given.
    """
    argv = list(tokens) if tokens is not None else sys.argv[1:]
    running = _RUNNING.set((app, tuple(argv)))
    try:
        try:
            result = app(
                argv,
                console=console,
                error_console=error_console,
                exit_on_error=False,
                print_error=False,
                result_action=result_action,
            )
        except CycloptsError as exc:
            note_requested_format(argv)
            raise_usage(parse_error(exc))
        except SystemExit as exc:
            exc.code = derived_exit_code(exc.code)
            raise
        exit_on_counted_failure()
    except KeyboardInterrupt:
        _flush_stdout()
        raise SystemExit(ExitCode.INTERRUPTED) from None
    except BrokenPipeError:
        # Pipe broke mid-write (output large enough to flush before we got here).
        _exit_broken_pipe()
    except SystemExit as exc:
        if isinstance(exc.__context__, BrokenPipeError) and _stdout_is_devnull():
            # Rich's ``Console.on_broken_pipe`` (help output) points stdout at
            # /dev/null and exits 1 from inside its ``except BrokenPipeError``;
            # a closed pipe exits 0. Any other exit keeps its code.
            _exit_broken_pipe()
        # cyclopts exits (0 on success) rather than returning. Flush buffered
        # stdout now so a broken pipe surfaces here — catchable — instead of at
        # interpreter shutdown, where it can't be handled.
        _flush_stdout()
        raise
    finally:
        _RUNNING.reset(running)
    _flush_stdout()
    return result


#: The app :func:`run_cyclopts_app` runs, and the tokens it was given.
_RUNNING: ContextVar[tuple[App, tuple[str, ...]] | None] = ContextVar(
    "untaped_running_app", default=None
)


@contextmanager
def given_tokens(app: App) -> Iterator[list[str] | None]:
    """The tokens :func:`run_cyclopts_app` was given to run ``app`` on; ``None`` otherwise.

    Cyclopts' parse rewrites a short cluster before a command sees its tokens
    (``-n5`` becomes ``-n -5``), so a command that reads the line itself
    reads these. A call nested in the block sees none, unless run anew.
    """
    running = _RUNNING.get()
    marker = _RUNNING.set(None)
    try:
        yield list(running[1]) if running is not None and running[0] is app else None
    finally:
        _RUNNING.reset(marker)


def _flush_stdout() -> None:
    """Flush stdout, converting a broken pipe into a clean exit."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _exit_broken_pipe()


def _stdout_is_devnull() -> bool:
    """Whether stdout's fd was redirected to ``/dev/null`` (Rich's broken-pipe hook)."""
    try:
        return os.path.samestat(os.fstat(sys.stdout.fileno()), os.stat(os.devnull))
    except AttributeError, OSError, ValueError:
        return False


def _exit_broken_pipe() -> NoReturn:
    """Silence the interpreter's final stdout flush, then exit 0 quietly.

    Redirecting the stdout fd to ``/dev/null`` stops Python re-raising the
    broken pipe when it flushes the standard streams on the way out. The guard
    covers streams with no real fd (a captured ``StringIO`` under tests)."""
    with suppress(OSError, ValueError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    raise SystemExit(0) from None


@contextmanager
def report_errors() -> Iterator[None]:
    """Convert :class:`UntapedError` into a clean stderr message + its exit code.

    Core's error boundary: the mount and the root dispatch run commands in
    it, so users see ``error: ...`` instead of a Python traceback. The exit code follows the error's
    ``category`` (``2`` usage, ``4`` environment, ``5`` unavailable, ``130``
    interrupted, ``1`` otherwise), raised to the most severe failure already
    reported in the run (:func:`~untaped.runtime.run_output.failure_exit_code`). A
    declined confirmation (:class:`~untaped.sdk.errors.OperationCancelledError`)
    prints its message without the ``error:`` prefix. :class:`~untaped.sdk.errors.Failed`
    and :class:`~untaped.sdk.errors.Matched` print nothing, since the rows
    said it: ``1`` and ``3``, a counted failure winning over either. Under JSON
    diagnostics the error is one JSON line (``Diagnostics.failure``).
    Non-:class:`UntapedError` exceptions are left to Cyclopts' default
    handling — those represent bugs we want to see — except a name a plugin
    reached that untaped or another plugin no longer has
    (:mod:`untaped.runtime.missing_names`): an error, exit ``4``, its
    traceback under ``--verbose``.
    """
    try:
        with _missing_names():
            yield
    except Failed as exc:
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
    except Matched as exc:
        raise SystemExit(derived_exit_code(exc.exit_code)) from exc
    except OperationCancelledError as exc:
        from untaped.sdk._emit import report_declined  # noqa: PLC0415 - rendering is heavy

        report_declined(exc)
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc
    except UntapedError as exc:
        Diagnostics().failure(exc)
        raise SystemExit(failure_exit_code(exc.exit_code)) from exc


#: Set on an exception a boundary has explained, so an outer one (the root's, around the
#: mount's) doesn't say it again.
_EXPLAINED = "__untaped_explained__"


@contextmanager
def _missing_names() -> Iterator[None]:
    """Raise a name a plugin missed as the error saying so; any other exception goes on as is."""
    try:
        yield
    except (AttributeError, ImportError, ValueError) as exc:
        from untaped.runtime.missing_names import explain, traceback_text  # noqa: PLC0415

        if getattr(exc, _EXPLAINED, False):  # an inner boundary said it already
            raise
        try:
            explained = explain(exc)
        except Exception:  # reading the error never hides it
            explained = None
        if explained is None:
            raise
        if isinstance(explained, str):  # it can't have gone: the traceback stays, named first
            Diagnostics().warning(explained)
            setattr(exc, _EXPLAINED, True)
            raise
        if is_verbose():
            Diagnostics().emit(Debug(logger="untaped", message=traceback_text(exc)))
        raise explained from exc
