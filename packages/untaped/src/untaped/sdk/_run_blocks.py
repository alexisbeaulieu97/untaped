"""The blocks that shape a run: Refresh, Parallel and Limit, and the table of all blocks."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping

from untaped.sdk._block import FieldBlock, FieldOption, Invocation
from untaped.sdk._confirm import Confirm
from untaped.sdk._input_blocks import Lines, Stdin
from untaped.sdk._keys import SettingKey
from untaped.sdk._output import Output, _copy
from untaped.sdk.errors import (
    ConfigError,
    UsageError,
)


class Refresh(FieldBlock):
    """Which caches ``--refresh`` / ``--cached`` are about: ``refresh: Refresh = Refresh(MOTD)``.

    Each argument is a cache on the plugin class or a cached method of a
    contract the plugin offers or fills; the help is generated from them.
    """

    field_kind = "refresh"
    options = (
        FieldOption(
            "--refresh",
            "bool",
            "--refresh checks upstream now; --cached reads what is cached and never checks.",
            negative="--cached",
        ),
    )

    def __init__(self, *caches: object) -> None:
        self.caches = caches
        self._directive: bool | None = None

    @property
    def directive(self) -> bool | None:
        """``True`` after ``--refresh``, ``False`` after ``--cached``, ``None`` otherwise.

        What a cache's ``serve(refresh=...)`` takes.
        """
        return self._directive

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Refresh:
        """A copy for one invocation, from ``--refresh`` / ``--cached``."""
        del call
        directive = flags.get("--refresh")
        filled = _copy(self)
        filled._directive = directive if isinstance(directive, bool) else None
        return filled


class Parallel(FieldBlock):
    """How many operations run at once: ``-j/--parallel``, ``Parallel(cap=10)``.

    ``default`` is a number or a ``key(...)`` to the setting that holds one;
    ``cap`` clamps the value with a warning. ``unset`` is the number an
    optional setting left unset stands for (``Parallel(default=key(S).parallel,
    unset=8, cap=16)``): the command's own default, so the cap lowers it
    without a warning.
    """

    field_kind = "parallel"
    options = (
        FieldOption(
            "--parallel", "int", "Maximum number of operations to run at once.", short="-j"
        ),
    )

    def __init__(
        self,
        *,
        default: int | SettingKey = 4,
        unset: int | None = None,
        cap: int | None = None,
    ) -> None:
        if any(isinstance(each, bool) for each in (default, unset, cap)):
            raise ValueError("Parallel's default, unset and cap are numbers, not booleans")
        if isinstance(default, int) and default < 1:
            raise ValueError("Parallel's default is at least 1")
        if unset is not None and not isinstance(default, SettingKey):
            raise ValueError("Parallel's unset is for a default read from a setting")
        if unset is not None and unset < 1:
            raise ValueError("Parallel's unset is at least 1")
        if cap is not None and cap < 1:
            raise ValueError("Parallel's cap is at least 1")
        self.default = default
        self.unset = unset
        self.cap = cap
        self._requested: int | None = None
        self._value: int | None = None
        #: Only a per-invocation copy keeps its value: an unfilled field is the
        #: class default every build shares, so it reads its setting afresh.
        self._filled = False

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Parallel:
        """A copy for one invocation, from ``-j/--parallel``; below 1 is a usage error."""
        del call
        requested = _count(flags, "--parallel")
        filled = _copy(self)
        filled._requested, filled._value, filled._filled = requested, None, True
        return filled

    @property
    def value(self) -> int:
        """How many operations run at once: ``-j``, else the default or its setting, capped.

        A value above ``cap`` is clamped with a warning, once per invocation;
        an unset setting's ``unset`` is lowered to ``cap`` silently. Read on
        an unfilled block under a run, it is the default (no ``-j``).
        """
        if self._value is not None:
            return self._value
        if self._requested is not None:
            value = self._clamped(self._requested, f"--parallel {self._requested}")
        else:
            value = self._from_default()
        if self._filled:
            self._value = value
        return value

    def _from_default(self) -> int:
        if isinstance(self.default, int):
            return self._clamped(self.default, f"the default {self.default}")
        from untaped.sdk._run import setting_name, settings_of  # noqa: PLC0415 - a setting pays

        model = SettingKey.model_of(self.default)
        value: object = settings_of(model)
        for part in SettingKey.path_of(self.default):
            value = getattr(value, part)
        if value is None and self.unset is not None:
            return self.unset if self.cap is None else min(self.unset, self.cap)
        setting = setting_name(self.default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(
                f"setting {setting} is {value!r}; it must be a number of at least 1",
                category="invalid",
            )
        return self._clamped(value, f"{setting} {value}")

    def map[ItemT, ResultT](
        self,
        fn: Callable[[ItemT], ResultT],
        items: Iterable[ItemT],
        *,
        ordered: bool = False,
        on_abort: Callable[[], None] | None = None,
        while_running: Callable[[], None] | None = None,
    ) -> Iterator[tuple[ItemT, ResultT]]:
        """Run ``fn`` on every item, :attr:`value` at a time, yielding ``(item, result)``.

        In completion order, so a slow first item never holds back the rest
        (``ordered=True`` keeps the input order). An exception from ``fn`` is
        raised here: catch it in ``fn`` to turn it into a failed row.
        ``on_abort`` runs when the work stops early (Ctrl-C, an exception,
        the iterator closed) for the calls in flight, such as killing their
        jobs; ``while_running`` does foreground work on this thread while the
        workers run, such as draining a queue they feed, and must return on
        its own: the pairs come after it.
        """
        from untaped.sdk._concurrency import bounded_iter  # noqa: PLC0415 - threads pay

        return bounded_iter(
            fn,
            items,
            concurrency=self.value,
            ordered=ordered,
            on_abort=on_abort,
            while_running=while_running,
        )

    def _clamped(self, value: int, source: str) -> int:
        """``value`` within the cap; ``source`` says where it came from in the warning."""
        if self.cap is None or value <= self.cap:
            return value
        from untaped.sdk._run import current_run  # noqa: PLC0415 - only a clamp pays

        current_run().diag.warning(f"{source} clamped to {self.cap} (this command's cap)")
        return self.cap


class Limit(FieldBlock):
    """``--limit N``: return at most this many results (unset: no limit).

    ``cap`` is the most a command can return (``Limit(default=30, cap=1000)``):
    a larger ``--limit`` is clamped with a warning, and help says the cap.
    """

    field_kind = "limit"
    options = (FieldOption("--limit", "int", "Return at most this many results."),)

    def __init__(self, *, default: int | None = None, cap: int | None = None) -> None:
        if isinstance(default, bool) or isinstance(cap, bool):
            raise ValueError("Limit's default and cap are numbers, not booleans")
        if default is not None and default < 1:
            raise ValueError("Limit's default is at least 1")
        if cap is not None and cap < 1:
            raise ValueError("Limit's cap is at least 1")
        if default is not None and cap is not None and default > cap:
            raise ValueError("Limit's default is at most its cap")
        self.default = default
        self.cap = cap
        self._requested: int | None = None
        self._clamped = False

    @property
    def value(self) -> int | None:
        """At most this many results; ``None`` is no limit.

        A ``--limit`` above ``cap`` is the cap, with a warning once per
        invocation.
        """
        if self._requested is None:
            return self.default
        if self.cap is None or self._requested <= self.cap:
            return self._requested
        if not self._clamped:
            from untaped.sdk._run import current_run  # noqa: PLC0415 - only a clamp pays

            current_run().diag.warning(
                f"--limit {self._requested} clamped to {self.cap} (this command's cap)"
            )
            self._clamped = True
        return self.cap

    def _option_default(self, option: FieldOption) -> str | int | None:
        return self.default if option.flag == "--limit" else None

    def _option_help(self, option: FieldOption) -> str:
        if self.cap is None or option.flag != "--limit":
            return option.help
        return f"{option.help.removesuffix('.')}, up to {self.cap}."

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Limit:
        """A copy for one invocation, from ``--limit``; below 1 is a usage error."""
        del call
        filled = _copy(self)
        filled._requested, filled._clamped = _count(flags, "--limit"), False
        return filled


def _count(flags: Mapping[str, object], flag: str) -> int | None:
    """The number ``flag`` was given, if any; below 1 is a usage error."""
    value = flags.get(flag)
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < 1:
        raise UsageError(f"{flag}: must be at least 1, got {value}")
    return value


FIELD_BLOCKS: tuple[type[FieldBlock], ...] = (
    Output,
    Stdin,
    Lines,
    Confirm,
    Refresh,
    Parallel,
    Limit,
)
"""Every field type core fills, in the order their panels list."""


#: The short flags core's blocks (and ``--help``) own; a command's ``Short`` may not take one.
CORE_SHORTS: frozenset[str] = frozenset(
    {"-h", *(option.short for block in FIELD_BLOCKS for option in block.options if option.short)}
)
