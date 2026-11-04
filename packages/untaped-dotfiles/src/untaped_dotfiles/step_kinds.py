"""Step kinds other plugins provide: the ``StepKind`` contract, and asking a kind's provider.

A package step written ``kind: macos.defaults`` with its fields under ``with:``
runs through the plugin that fills :class:`StepKind` with the record whose
kind that is. Dotfiles never names a provider: it lists who fills the
contract (``providers``), finds the one issuing the kind, and asks it alone
through ``gather``, with an approved step only (:mod:`~untaped_dotfiles.step_runner`).
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, JsonValue, SecretStr, ValidationError

from untaped.sdk import ConfigError, Issued, Record, experimental, first_validation_error, plural, q
from untaped.sdk.contracts import (
    Contract,
    Errored,
    Ok,
    ProviderOffer,
    gather,
    providers,
)
from untaped.sdk.health import DoctorResult, PluginContext
from untaped_dotfiles.errors import DotfilesError

if TYPE_CHECKING:
    from untaped_dotfiles.package import Step
    from untaped_dotfiles.ports import Installed


@experimental
class StepResult(BaseModel):
    """What a step kind's check or run found."""

    passes: bool = Field(description="Whether the machine is as the step wants it now.")
    detail: str = Field("", description="Why not, said on the step's row.")


@experimental
class StepFields(Issued):
    """No fields: the record a provider replaces with its own; one that keeps it adds no kind."""


@experimental
class StepKind[W: Record = StepFields](Contract):
    """One kind of step a package can use: ``kind: <its kind>``, its fields under ``with:``.

    Fill it with your own record ``W`` (``StepKind[Defaults]``): the kind's
    name is ``W``'s kind, which core gives it (``macos.defaults`` for the class
    ``Defaults`` in the plugin ``macos``), ``with:`` is validated as ``W``,
    and ``W``'s docstring says what the kind does. A plugin with three kinds
    fills three providers. A kind's name is written into package files that
    people share, so renaming ``W`` breaks them.

    ``check`` reads the machine as it is now and ``run`` changes it; dotfiles
    calls them only for a step this machine approved, never caches them, and
    runs ``check`` again after ``run``. ``run`` gets the value of the step's
    ``stdin`` input as ``secret`` (leave the parameter out when the kind takes
    none). Neither has a terminal: a step never prompts.
    """

    @abstractmethod
    def check(self, step: W) -> StepResult:
        """Whether the machine is as ``step`` wants it."""

    @abstractmethod
    def run(self, step: W, secret: SecretStr | None = None) -> StepResult:
        """Make the machine as ``step`` wants it; ``passes`` false fails the step's row."""


@dataclass(frozen=True)
class KindProvider:
    """The provider that issues one kind, and its record of ``with:``."""

    provider: type[Contract]
    record: type[Record]
    plugin: str


class StepKinds:
    """Finds each step kind's provider (listed once per command) and asks it.

    ``installed`` gives the plugins this run knows, so a kind whose plugin is
    installed but disabled says so rather than "install it".
    """

    def __init__(self, installed: Callable[[], Sequence[Installed]] | None = None) -> None:
        self._installed = installed

    @cached_property
    def _offers(self) -> list[ProviderOffer]:
        return providers(StepKind)

    @cached_property
    def _kinds(self) -> dict[str, ProviderOffer]:
        """Each kind and the offer issuing it: one, since a kind names its plugin, and core
        refuses two providers of one record in a plugin."""
        return {offer.kind: offer for offer in self._offers if offer.kind is not None}

    def unavailable(self, kind: str) -> str:
        """Why no provider can be asked for ``kind``; ``""`` when one can."""
        if kind in self._kinds:
            return ""
        plugin = kind.split(".", 1)[0]
        refused = [o for o in self._offers if o.plugin == plugin and o.refused is not None]
        if refused:
            first = refused[0]
            fix = f"; {first.fix}" if first.fix else ""
            return (
                f"kind {kind}: the plugin {plugin} has a step kind untaped refused "
                f"({first.refused}): {first.detail}{fix}"
            )
        state = self._plugin_state(plugin)
        if state == "quarantined":
            return f"kind {kind}: the plugin {plugin} is disabled; `untaped doctor` says why"
        if state == "ready":
            offered = sorted(k for k in self._kinds if k.startswith(f"{plugin}."))
            listed = f"; it provides {', '.join(offered)}" if offered else ""
            return f"kind {kind}: the plugin {plugin} provides no such step kind{listed}"
        return f"kind {kind}: no installed plugin provides it; install the plugin {plugin}"

    def _plugin_state(self, plugin: str) -> str | None:
        if self._installed is None:
            return None
        return next((p.status for p in self._installed() if p.name == plugin), None)

    def provider(self, kind: str) -> KindProvider:
        """The one provider of ``kind``; raises :class:`ConfigError` (exit 4) when there is none."""
        why = self.unavailable(kind)
        if why:
            raise ConfigError(why)
        offer = self._kinds[kind]
        assert offer.provider is not None and offer.record is not None  # a kind is a taken offer
        return KindProvider(offer.provider, offer.record, offer.plugin)

    def names(self) -> list[str]:
        """Every kind some provider issues."""
        return sorted(self._kinds)

    def unclaimed(self) -> list[ProviderOffer]:
        """The providers that keep ``StepFields``: they add no kind and are never asked."""
        return [o for o in self._offers if o.refused is None and o.kind is None]

    @staticmethod
    def fields(step: Step, record: type[Record], text: Callable[[str, str], str]) -> Record:
        """``with:`` rendered by ``text(value, at)`` and validated as ``record``."""
        rendered = _rendered(step.with_, text, "with")
        try:
            return record.model_validate(rendered)
        except ValidationError as exc:
            raise DotfilesError(
                f"step {q(step.name)}: with: {first_validation_error(exc)}"
            ) from exc

    def check(self, found: KindProvider, fields: Record) -> StepResult:
        return self._asked(found, StepKind.check, fields)

    def run(self, found: KindProvider, fields: Record, secret: SecretStr | None) -> StepResult:
        return self._asked(found, StepKind.run, fields, secret)

    @staticmethod
    def _asked(found: KindProvider, method: Callable[..., StepResult], *args: object) -> StepResult:
        """Ask ``found``'s provider alone: its answer, its error as raised, or setup (exit 4)."""
        [answer] = gather(method, plugins={found.provider})(*args)
        if isinstance(answer, Ok):
            return answer.value
        if isinstance(answer, Errored):
            raise answer.error
        raise ConfigError(f"{found.plugin} can't run this step: {answer.detail}")


def _rendered(value: JsonValue, text: Callable[[str, str], str], at: str) -> JsonValue:
    """``value`` with every string rendered, keeping its shape."""
    if isinstance(value, str):
        return text(value, at)
    if isinstance(value, Mapping):
        return {key: _rendered(each, text, f"{at}.{key}") for key, each in value.items()}
    if isinstance(value, list):
        return [_rendered(each, text, f"{at}[{n}]") for n, each in enumerate(value)]
    return value


def step_kinds_check(_ctx: PluginContext) -> DoctorResult:
    """``dotfiles.step-kinds``: warns about a provider that adds no kind (core's own rows say
    which offers it refused)."""
    kinds = StepKinds()
    problems = [
        f"{offer.plugin}'s {getattr(offer.provider, '__qualname__', 'provider')} keeps "
        "StepFields, so it adds no step kind and is never asked; give it a record of its own "
        "(StepKind[YourRecord])"
        for offer in kinds.unclaimed()
    ]
    if problems:
        return DoctorResult(ok=True, warn=True, detail="; ".join(problems))
    found = len(kinds.names())
    return DoctorResult(ok=True, detail=f"{plural(found, 'step kind')} from other plugins")


__all__ = ["KindProvider", "StepFields", "StepKind", "StepKinds", "StepResult", "step_kinds_check"]
