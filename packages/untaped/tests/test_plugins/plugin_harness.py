"""Shared factories for plugin composition tests (not collected)."""

from __future__ import annotations

import itertools
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from cyclopts import App
from pydantic import BaseModel

from untaped.cli import create_app
from untaped.plugins.registry import (
    PLUGINS_ENTRY_POINT_GROUP,
    ApplicationSpec,
    DoctorCheck,
    DoctorResult,
    PluginCandidate,
    PluginSpec,
    SkillAsset,
)


class Profile(BaseModel):
    token: str = "default-token"
    region: str = "us"


class OtherProfile(BaseModel):
    endpoint: str = "https://example.invalid"
    retries: int = 3


class State(BaseModel):
    last_run: str = ""


def make_app(name: str = "test-app") -> App:
    return create_app(name=name, help=f"{name} help")


def nullary_app(name: str = "test-app") -> Callable[[], App]:
    def _factory() -> App:
        return make_app(name)

    return _factory


def make_skill(name: str = "test-skill") -> SkillAsset:
    return SkillAsset(name=name, source=Path("/tmp/test-skill"), description=f"{name} skill")


def make_check(
    id: str = "test.check",
    title: str = "Test check",
    record_calls: list[str] | None = None,
) -> DoctorCheck:
    def _run(ctx: Any) -> DoctorResult:
        if record_calls is not None:
            record_calls.append(id)
        return DoctorResult(id=id, ok=True, detail="fine")

    return DoctorCheck(id=id, title=title, run=_run)


def exploding_check(id: str = "test.boom") -> DoctorCheck:
    def _run(ctx: Any) -> DoctorResult:
        raise AssertionError("doctor body must never run at compose time")

    return DoctorCheck(id=id, title="Boom", run=_run)


def make_spec(
    name: str = "alpha",
    settings: type[BaseModel] | None = Profile,
    state: type[BaseModel] | None = None,
    skills: tuple[SkillAsset, ...] = (),
    checks: tuple[DoctorCheck, ...] = (),
    factory: Callable[[], App] | None = None,
) -> PluginSpec:
    return PluginSpec(
        name=name,
        app_factory=factory or nullary_app(f"{name}-app"),
        settings=settings,
        state=state,
        skills=skills,
        doctor_checks=checks,
    )


def make_shell(
    name: str = "untaped",
    section: str = "shell",
    skills: tuple[SkillAsset, ...] = (),
    checks: tuple[DoctorCheck, ...] = (),
) -> ApplicationSpec:
    return ApplicationSpec(
        name=name,
        app_factory=nullary_app("root"),
        section=section,
        settings=OtherProfile,
        skills=skills,
        doctor_checks=checks,
    )


_TARGETS = itertools.count()


def spec_target(spec: PluginSpec, *, error: Exception | None = None, result: Any = None) -> object:
    """A candidate target resolving to ``spec``, to ``result``, or raising ``error``.

    Plain specs are their own target; the others are a ``module:SPEC`` string
    naming a synthetic module whose ``SPEC`` attribute raises or returns.
    """
    if error is None and result is None:
        return spec
    name = f"untaped_test_target_{next(_TARGETS)}"
    module = ModuleType(name)

    def attribute(key: str) -> Any:
        if error is not None:
            raise error
        return result

    module.__getattr__ = attribute  # type: ignore[method-assign]  # resolved per access
    sys.modules[name] = module
    return f"{name}:SPEC"


def make_candidate(
    spec: PluginSpec,
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
        name=name or spec.name,
        target=spec_target(spec, **kwargs),
        distribution_version=distribution_version,
        entry_point_group=entry_point_group,
        requires_dist=tuple(requires_dist),
    )
