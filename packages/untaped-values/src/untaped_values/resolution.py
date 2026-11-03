"""Where each input's value comes from: the given mappings, the default, then an answer.

:func:`resolve` holds the one precedence and the one refusal every consumer
shares; :func:`terminal_ask` is the answer a command's terminal gives, or
``None`` when nothing may be asked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from untaped.sdk import experimental
from untaped_values.errors import InputError
from untaped_values.inputs import Input, coerce
from untaped_values.ports import Ask

if TYPE_CHECKING:
    from untaped.sdk import Terminal
    from untaped_values.options import Vars

#: Types a person can't answer at a prompt: given by flag or file, or reported missing.
_UNASKED = frozenset({"list", "dict"})


@experimental
@dataclass(frozen=True)
class Resolved:
    """The values found, the required inputs still without one, and those asked for."""

    values: dict[str, object]
    #: Every required input that has no value, in declaration order; nothing was asked.
    missing: tuple[str, ...] = ()
    #: The inputs whose value came from an answer.
    asked: tuple[str, ...] = ()


@experimental
def resolve[S: Input](
    specs: Mapping[str, S],
    given: Sequence[Mapping[str, object]],
    *,
    known: Iterable[str] = (),
    ask: Ask | None = None,
    label: str = "",
) -> Resolved:
    """Each input's value: the first ``given`` mapping that names it, its default, else an answer.

    ``given`` is in precedence order (flags, then files, then configuration,
    as the caller has them). A name in ``given`` that neither ``specs`` nor
    ``known`` (names other files in the same run declare) declares is
    refused. An input that isn't required and has no value is left out.

    When a required input has no value, ``ask`` answers it (``label`` says
    for what, such as a target). Nothing is asked when ``ask`` is ``None`` or
    one of them can't be asked (a ``list`` or ``dict``): ``missing`` then
    names every one at once, and the caller says how to give them.
    """
    declared = {*specs, *known}
    for mapping in given:
        unknown = sorted(set(mapping) - declared)
        if unknown:
            names = ", ".join(unknown)
            listed = ", ".join(sorted(declared)) or "(none)"
            word = "input" if len(unknown) == 1 else "inputs"
            raise InputError(f"unknown {word}: {names}; declared: {listed}")
    values: dict[str, object] = {}
    pending: list[str] = []
    for name, spec in specs.items():
        source = next((mapping for mapping in given if name in mapping), None)
        if source is not None:
            values[name] = coerce(spec, source[name], name=name)
        elif spec.default is not None:
            values[name] = coerce(spec, spec.default, name=name)
        elif spec.is_required():
            pending.append(name)
    if not pending:
        return Resolved(values)
    if ask is None or any(specs[name].type in _UNASKED for name in pending):
        return Resolved(values, missing=tuple(pending))
    for name in pending:
        values[name] = coerce(specs[name], ask(name, specs[name], label=label), name=name)
    return Resolved(values, asked=tuple(pending))


@experimental
def terminal_ask(terminal: Terminal, vars: Vars) -> Ask | None:
    """Ask at ``terminal``: hidden for a secret, a pick for a choice; ``None`` when nothing may ask.

    ``None`` when the run can't prompt (no terminal on stdin) or
    ``--non-interactive`` was given. The prompt reads ``NAME for LABEL
    (description)``.
    """
    if vars.non_interactive or not terminal.can_prompt:
        return None

    def ask(name: str, spec: Input, *, label: str) -> object:
        message = name if not label else f"{name} for {label}"
        if spec.description:
            message = f"{message} ({spec.description})"
        if spec.secret:
            return terminal.secret(message)
        if spec.type == "choice":
            from untaped.sdk.prompt import PromptChoice  # noqa: PLC0415 - only when asked

            options = [PromptChoice(value=choice, label=str(choice)) for choice in spec.choices]
            return terminal.select(message, options)
        return terminal.text(message)

    return ask
