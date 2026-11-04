"""Running a step: its check, and what fixes it, only as approved.

Nothing runs a step's command but :class:`StepRunner`, and it takes only an
:class:`ApprovedStep`, which comes from exactly two places: a ``steps`` row
(the step as last applied, unchanged in the source) or a step in a plan that
was confirmed. Planning a step nobody approved runs nothing.

A program runs with no shell, in the home folder, with the environment
untaped received; an argument that starts with ``~/`` names the home folder.
Its stdin is ``/dev/null`` or the value of the step's ``stdin`` input.
Checks stop after 30 seconds, runs after 10 minutes. What a run prints is
relayed with every secret value of the run masked.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import SecretStr

from untaped.sdk import Record, UntapedError, q
from untaped.sdk.process import ProcessResult, run_process
from untaped_dotfiles.errors import DotfilesError
from untaped_values.api import InputError, render

if TYPE_CHECKING:
    from untaped_dotfiles.models import StepRecord
    from untaped_dotfiles.package import PackageInput, Step
    from untaped_dotfiles.step_kinds import StepKinds

CHECK_TIMEOUT = 30.0
RUN_TIMEOUT = 600.0

_APPROVAL = object()
"""Held only by the two constructors of :class:`ApprovedStep`."""


#: What doesn't change what a step does: editing it leaves the step approved.
_NOT_APPROVED = {"description", "os", "only", "unless"}


def step_digest(step: Step, script_hash: str | None) -> str:
    """What approving ``step`` approves: what it checks, runs and reads, and its script."""
    declared = step.model_dump(mode="json", by_alias=True, exclude=_NOT_APPROVED)
    body = json.dumps({"step": declared, "script": script_hash}, sort_keys=True)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovedStep:
    """A step this machine approved, with the values its text fields render."""

    key: str
    step: Step
    specs: Mapping[str, PackageInput]
    """Every input the step may name: the package's and its own."""
    values: Mapping[str, object]
    """The plain values its text fields render."""
    script: Path | None = None
    """A ``script`` step: the file in the source that runs."""
    _approval: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._approval is not _APPROVAL:
            raise TypeError("an ApprovedStep comes from a steps row or a confirmed plan only")

    @classmethod
    def from_record(
        cls,
        record: StepRecord,
        *,
        specs: Mapping[str, PackageInput],
        script: Path | None,
    ) -> ApprovedStep:
        """The step as last applied, rendered with the values it used then."""
        return cls(record.step, record.declared, specs, dict(record.values), script, _APPROVAL)

    @classmethod
    def from_plan(
        cls,
        key: str,
        step: Step,
        *,
        confirmed: Literal[True],
        specs: Mapping[str, PackageInput],
        values: Mapping[str, object],
        script: Path | None,
    ) -> ApprovedStep:
        """A step of a plan that was just confirmed (at the prompt, or by ``--yes``)."""
        if confirmed is not True:
            raise TypeError("only a confirmed plan approves a step")
        return cls(key, step, specs, dict(values), script, _APPROVAL)

    def text(self, value: str, *, at: str) -> str:
        try:
            return render(value, self.values, self.specs, field=at)
        except InputError as exc:
            raise DotfilesError(f"step {q(self.step.name)}: {exc}") from exc

    @property
    def argv(self) -> list[str] | None:
        """What a run executes; ``None`` for a report-only step."""
        step = self.step
        if step.run is not None:
            return [self.text(each, at="run") for each in step.run]
        if self.script is not None:
            return [str(self.script), *(self.text(each, at="args") for each in step.args)]
        return None


@dataclass(frozen=True)
class Checked:
    """Whether a check passes, and why not."""

    passed: bool
    detail: str = ""


class StepRunner:
    """Runs approved steps' checks and runs; ``relay`` gets what a run prints, masked."""

    def __init__(
        self,
        *,
        home: Path,
        relay: Callable[[str], None] | None = None,
        kinds: StepKinds | None = None,
    ) -> None:
        self._home = home
        self._relay = relay
        self.kinds = kinds if kinds is not None else _no_installed_kinds()
        """Where a ``kind:`` step's provider is found and asked."""

    def with_relay(self, relay: Callable[[str], None]) -> StepRunner:
        return StepRunner(home=self._home, relay=relay, kinds=self.kinds)

    def check(self, approved: ApprovedStep, *, secrets: Sequence[str] = ()) -> Checked:
        """Run the step's check (or its kind's); ``secrets`` are masked in what a kind says."""
        kind = approved.step.kind
        if kind is not None:
            provider = self.kinds.provider(kind)
            with _masking(secrets):
                result = self.kinds.check(provider, self._fields(approved, provider.record))
            return Checked(
                result.passes, _masked(result.detail, secrets) or f"its {kind} check fails"
            )
        check = approved.step.check
        assert check is not None  # a step without a check has a kind
        if check.path is not None:
            text = approved.text(check.path, at="check.path")
            path = self._home_path(text)
            if not path.is_absolute():
                raise DotfilesError(
                    f"step {q(approved.step.name)}: check path {text!r} is neither `~/...` "
                    "nor absolute"
                )
            return Checked(True) if path.exists() else Checked(False, f"{path} is absent")
        if check.program is not None:
            found = shutil.which(check.program)
            return Checked(found is not None, f"{check.program} is not on PATH")
        assert check.command is not None
        argv = [self._home_text(approved.text(each, at="check.command")) for each in check.command]
        ended = self._process(argv, timeout=CHECK_TIMEOUT, what="check", stdin=None)
        if ended.returncode != 0:
            return Checked(False, f"`{_shown(argv)}` exits {ended.returncode}")
        if check.prints is None:
            return Checked(True)
        printed = ended.stdout.decode("utf-8", "replace").strip()
        wanted = approved.text(check.prints, at="check.prints")
        if printed == wanted:
            return Checked(True)
        return Checked(False, f"`{_shown(argv)}` doesn't print {wanted!r}")

    def run(self, approved: ApprovedStep, *, stdin: bytes | None, secrets: Sequence[str]) -> None:
        """Run the step's program (or its kind); a run that doesn't exit 0 (or pass) raises."""
        kind = approved.step.kind
        if kind is not None:
            provider = self.kinds.provider(kind)
            secret = None if stdin is None else SecretStr(stdin.decode("utf-8"))
            with _masking(secrets):  # what it says is shown as a run's output is: masked
                result = self.kinds.run(provider, self._fields(approved, provider.record), secret)
            if not result.passes:
                detail = _masked(result.detail, secrets) or "it says it didn't pass"
                raise DotfilesError(f"the {kind} run failed: {detail}")
            return
        assert approved.argv is not None  # report-only steps never run
        argv = [self._home_text(each) for each in approved.argv]
        ended = self._process(argv, timeout=RUN_TIMEOUT, what="run", stdin=stdin)
        self._relayed(ended, secrets)
        if ended.returncode != 0:
            raise DotfilesError(f"`{_shown(argv)}` exits {ended.returncode}")

    def _fields(self, approved: ApprovedStep, record: type[Record]) -> Record:
        return self.kinds.fields(approved.step, record, lambda v, at: approved.text(v, at=at))

    def _process(
        self, argv: list[str], *, timeout: float, what: str, stdin: bytes | None
    ) -> ProcessResult:
        try:
            ended = run_process(argv, timeout=timeout, cwd=self._home, input=stdin)
        except OSError as exc:
            raise DotfilesError(
                f"cannot start {q(argv[0])}: {exc.strerror or exc}", category="not_found"
            ) from exc
        if ended.ended == "timed_out":
            raise DotfilesError(f"the {what} `{_shown(argv)}` took over {timeout:.0f} s")
        if ended.ended == "stopped":
            raise DotfilesError(f"the {what} `{_shown(argv)}` was stopped")
        return ended

    def _relayed(self, ended: ProcessResult, secrets: Sequence[str]) -> None:
        if self._relay is None:
            return
        for stream in (ended.stdout, ended.stderr):
            text = _masked(stream.decode("utf-8", "replace"), secrets)
            for line in text.splitlines():
                self._relay(line)

    def _home_path(self, text: str) -> Path:
        """``~`` and ``~/...`` in the home folder; anything else as written."""
        if text == "~":
            return self._home
        if text.startswith("~/"):
            return self._home / text[2:]
        return Path(text)

    def _home_text(self, text: str) -> str:
        """An argv element: one that starts with ``~/`` (or is ``~``) names the home folder."""
        return str(self._home_path(text)) if text == "~" or text.startswith("~/") else text


def _masked(text: str, secrets: Sequence[str]) -> str:
    """``text`` with every secret value of the run masked."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _masked_value(value: object, secrets: Sequence[str]) -> object:
    """``value`` (an error's ``details``) with every secret in its strings masked."""
    if isinstance(value, str):
        return _masked(value, secrets)
    if isinstance(value, Mapping):
        return {key: _masked_value(each, secrets) for key, each in value.items()}
    if isinstance(value, list | tuple):
        return [_masked_value(each, secrets) for each in value]
    return value


@contextmanager
def _masking(secrets: Sequence[str]) -> Iterator[None]:
    """An error a step kind's provider raises, re-raised with every secret value masked.

    Its message, hint and details; the original is dropped (``from None``) so no
    traceback carries it.
    """
    try:
        yield
    except UntapedError as exc:
        message = _masked(str(exc), secrets)
        hint = None if exc.hint is None else _masked(exc.hint, secrets)
        details = _masked_value(dict(exc.details), secrets)
        if (message, hint, details) == (str(exc), exc.hint, dict(exc.details)):
            raise
        assert isinstance(details, dict)
        raise UntapedError(
            message, category=exc.category, system=exc.system, hint=hint, details=details
        ) from None


def _shown(argv: Sequence[str]) -> str:
    return " ".join(argv)


def _no_installed_kinds() -> StepKinds:
    """Step kinds found among the providers alone, not knowing which plugins are installed."""
    from untaped_dotfiles.step_kinds import StepKinds  # noqa: PLC0415 - contracts on use

    return StepKinds()
