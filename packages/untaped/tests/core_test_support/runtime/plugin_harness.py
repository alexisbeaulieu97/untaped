"""Shared factories for plugin composition tests (not collected)."""

from __future__ import annotations

import itertools
import sys
from types import ModuleType
from typing import Any

from pydantic import BaseModel

from untaped.runtime.registry import PLUGINS_ENTRY_POINT_GROUP, PluginCandidate
from untaped.sdk import Plugin
from untaped.sdk._names import kebab
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult


class Profile(BaseModel):
    token: str = "default-token"
    region: str = "us"


class OtherProfile(BaseModel):
    endpoint: str = "https://example.invalid"
    retries: int = 3


def make_check(
    id: str = "test.check",
    title: str = "Test check",
    record_calls: list[str] | None = None,
) -> DoctorCheck:
    def _run(ctx: Any) -> DoctorResult:
        if record_calls is not None:
            record_calls.append(id)
        return DoctorResult(id=id, ok=True, detail="fine")

    plugin, _, name = id.partition(".")
    return DoctorCheck(title=title, run=_run)._spelled_as(plugin, name)


_TARGETS = itertools.count()


def plugin_name(spec: type[Plugin]) -> str:
    """A plugin class's name: its class name in kebab case."""
    return kebab(spec.__name__)


def spec_target(
    spec: type[Plugin],
    *,
    error: Exception | None = None,
    result: Any = None,
    calls: list[str] | None = None,
) -> object:
    """A candidate target resolving to ``spec``, to ``result``, or raising ``error``.

    A plain class is its own target; otherwise it is a ``package.plugin:SPEC``
    string naming a synthetic leaf module, as an installed plugin's entry point
    does, whose ``SPEC`` attribute raises or returns, and appends the class's
    name to ``calls`` each time it is resolved.
    """
    if error is None and result is None and calls is None:
        return spec
    package = ModuleType(f"untaped_test_target_{next(_TARGETS)}")
    package.__path__ = []
    sys.modules[package.__name__] = package
    name = f"{package.__name__}.plugin"
    module = ModuleType(name)

    def attribute(key: str) -> Any:
        if key.startswith("__"):  # a module's own (``__path__``): it has none
            raise AttributeError(key)
        if calls is not None:
            calls.append(plugin_name(spec))
        if error is not None:
            raise error
        return spec if result is None else result

    module.__getattr__ = attribute  # type: ignore[method-assign]  # resolved per access
    sys.modules[name] = module
    return f"{name}:SPEC"


def make_candidate(
    spec: type[Plugin],
    distribution: str = "example-dist",
    name: str | None = None,
    *,
    distribution_version: str = "",
    entry_point_group: str = PLUGINS_ENTRY_POINT_GROUP,
    requires_dist: tuple[str, ...] | list[str] = (),
    **kwargs: Any,
) -> PluginCandidate:
    return PluginCandidate(
        distribution=distribution,
        name=name or plugin_name(spec),
        target=spec_target(spec, **kwargs),
        distribution_version=distribution_version,
        entry_point_group=entry_point_group,
        requires_dist=tuple(requires_dist),
    )
