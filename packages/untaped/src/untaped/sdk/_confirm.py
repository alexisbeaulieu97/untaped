"""The Confirm and DryRun blocks: a command's preview, and the yes it waits for before it acts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack

from untaped.sdk._block import FieldBlock, FieldOption, Invocation
from untaped.sdk._output import PreviewItem, _copy
from untaped.sdk.errors import (
    NeedsYesError,
    OperationCancelledError,
    UsageError,
)


class Confirm(FieldBlock):
    """This command asks before it commits: adds ``--yes`` and ``--dry-run``.

    ``run`` calls it once, where it is about to commit:
    ``if self.confirm("Delete profile 'x'?", preview=lambda: lines): ...``.
    """

    field_kind = "confirm"
    panel = "Confirmation"
    options = (
        FieldOption("--yes", "bool", "Go ahead without asking.", short="-y"),
        FieldOption("--dry-run", "bool", "Show what would change and change nothing."),
    )

    def __init__(self) -> None:
        self._yes = False
        self._dry_run = False
        #: The command line asking (``untaped hello delete``); ``None`` while unfilled.
        self._command: str | None = None

    @property
    def yes(self) -> bool:
        """``--yes`` was passed."""
        return self._yes

    @property
    def dry_run(self) -> bool:
        """``--dry-run`` was passed."""
        return self._dry_run

    def as_dry_run(self) -> Confirm:
        """The same answers with ``--dry-run`` on: what a ``--check`` flag passes on.

        Noted on the current run, so core treats the invocation as a preview:
        call it at the top of ``run``, before any early return.
        """
        from untaped.sdk._run import note_preview  # noqa: PLC0415 - only a --check pays

        note_preview()
        dry = _copy(self)
        dry._dry_run = True
        return dry

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Confirm:
        """A copy for one invocation, from ``--yes`` and ``--dry-run``."""
        filled = _copy(self)
        filled._yes = flags.get("--yes") is True
        filled._dry_run = flags.get("--dry-run") is True
        filled._command = " ".join(call.words)
        return filled

    def ensure_answerable(self, question: str) -> None:
        """Refuse now when ``question`` could never be answered, before work the ask would waste.

        For a command that asks only after slow or side-effecting work (running
        hooks to plan): without a terminal, ``--yes`` or ``--dry-run`` it raises
        the :class:`NeedsYesError` (exit 2) asking would raise later; otherwise
        nothing happens, and the command asks as usual.
        """
        from untaped.sdk._run import current_run  # noqa: PLC0415 - only a command asking pays

        if self._dry_run or self._yes:
            return
        if self._command is None:
            raise NeedsYesError(question)
        try:
            with current_run().terminal().terminal():
                pass
        except UsageError:
            raise NeedsYesError(question, command=self._command) from None

    def __call__(
        self,
        question: str,
        *,
        preview: Callable[[], Iterable[PreviewItem]] | None = None,
        needed: bool = True,
    ) -> bool:
        """Whether to carry out what the command is about to do.

        ``True``: go ahead. ``False`` only under ``--dry-run``, after the preview:
        nothing happens, and the command prints its planned rows. A decline never
        returns (exit 1); without a terminal and ``--yes`` it raises
        :class:`NeedsYesError` (exit 2), and so does an unfilled field, so a command
        built outside a run never goes ahead unasked. ``preview`` returns what is
        printed on stderr before the question (lines, or records as a table);
        ``--yes`` and ``needed=False`` skip it.
        """
        from untaped.sdk._run import current_run  # noqa: PLC0415 - only a command asking pays

        if self._dry_run:
            _print_preview(preview)
            return False
        if not needed or self._yes:
            return True
        if self._command is None:
            raise NeedsYesError(question)
        terminal = current_run().terminal()
        with ExitStack() as stack:
            try:
                stack.enter_context(terminal.terminal())
            except UsageError:
                raise NeedsYesError(question, command=self._command) from None
            _print_preview(preview)
            if not terminal.confirm(question):
                raise OperationCancelledError
        return True


def _print_preview(preview: Callable[[], Iterable[PreviewItem]] | None) -> None:
    """The preview on stderr, unmuted: what the user decides on or would get."""
    if preview is None:
        return
    from untaped.sdk._run import current_run  # noqa: PLC0415 - only a preview pays

    diag = current_run().diag
    for line in preview():
        diag.info(line, unmuted=True)


class DryRun(FieldBlock):
    """This command writes without asking and can preview instead: adds ``--dry-run`` alone.

    ``if self.dry_run.go_ahead(preview=items): write()`` prints the preview
    (lines, or rows as a table) as :class:`Confirm` does, and writes nothing
    after ``--dry-run``; ``if self.dry_run: ...`` is true after ``--dry-run``.
    A command that asks first takes :class:`Confirm`, which has its own
    ``--dry-run``; one command takes one of the two.
    """

    field_kind = "dry_run"
    panel = "Confirmation"
    options = (FieldOption("--dry-run", "bool", "Show what would change and change nothing."),)

    def __init__(self) -> None:
        self._dry_run = False

    def __bool__(self) -> bool:
        """``--dry-run`` was passed."""
        return self._dry_run

    def go_ahead(self, preview: Callable[[], Iterable[PreviewItem]] | None = None) -> bool:
        """``True``: write. After ``--dry-run``, ``False`` once the preview is on stderr."""
        if not self._dry_run:
            return True
        _print_preview(preview)
        return False

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> DryRun:
        """A copy for one invocation, from ``--dry-run``."""
        del call
        filled = _copy(self)
        filled._dry_run = flags.get("--dry-run") is True
        return filled
