"""Shared factories for capability composition tests (not collected)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from cyclopts import App
from pydantic import BaseModel

from untaped.capabilities.registry import (
    CAPABILITIES_ENTRY_POINT_GROUP,
    ApplicationSpec,
    CapabilitySpec,
    DoctorCheck,
    DoctorResult,
    ExternalProvider,
    SkillAsset,
)
from untaped.cli import create_app


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
    section: str | None = None,
    profile: type[BaseModel] = Profile,
    state: type[BaseModel] | None = None,
    skills: tuple[SkillAsset, ...] = (),
    checks: tuple[DoctorCheck, ...] = (),
    factory: Callable[[], App] | None = None,
) -> CapabilitySpec:
    return CapabilitySpec(
        name=name,
        app_factory=factory or nullary_app(f"{name}-app"),
        config_section=section or name,
        profile_model=profile,
        state_model=state,
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
        config_section=section,
        profile_model=OtherProfile,
        state_model=None,
        skills=skills,
        doctor_checks=checks,
    )


class Provider:
    """Configurable external provider double."""

    def __init__(
        self,
        spec: CapabilitySpec | None = None,
        *,
        api_requires: Any = (1.0, 2.0),
        error: Exception | None = None,
        result: Any = None,
    ) -> None:
        self.api_requires = api_requires
        self._spec = spec
        self._error = error
        self._result = result

    def __call__(self) -> Any:
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        assert self._spec is not None
        return self._spec


def make_external(
    spec: CapabilitySpec,
    distribution: str = "example-dist",
    name: str | None = None,
    *,
    distribution_version: str = "",
    entry_point_group: str = CAPABILITIES_ENTRY_POINT_GROUP,
    requires_dist: tuple[str, ...] | list[str] = (),
    **kwargs: Any,
) -> ExternalProvider:
    return ExternalProvider(
        distribution=distribution,
        name=name or spec.name,
        target=Provider(spec, **kwargs),
        distribution_version=distribution_version,
        entry_point_group=entry_point_group,
        requires_dist=tuple(requires_dist),
    )


def function_provider(
    spec: CapabilitySpec, *, api_requires: Any = (1.0, 2.0)
) -> Callable[[], CapabilitySpec]:
    def _provide() -> CapabilitySpec:
        return spec

    _provide.api_requires = api_requires  # type: ignore[attr-defined]
    return _provide
