"""Shared doubles for root management-surface tests (Wave 1.4)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cyclopts import App
from pydantic import BaseModel, SecretStr

from untaped import bootstrap
from untaped.capabilities.registry import (
    CapabilitySpec,
    CompositionResult,
    DoctorCheck,
    DoctorResult,
    ProviderRef,
    RegisteredCapability,
    SkillAsset,
)
from untaped.cli import create_app
from untaped.settings import get_settings

# NOTE: section models are module-level on purpose. The settings registry
# rejects re-registration of a section under a *different* model object, so
# every test reuses these shared classes per section name.


class GithubProfile(BaseModel):
    """Fake capability profile model (section ``github``)."""

    token: SecretStr | None = None
    base_url: str = "https://api.github.com"
    mode: Literal["on", "off"] | None = None


class GithubState(BaseModel):
    """Fake capability state model (section ``github``)."""

    cursor: str | None = None


class JiraProfile(BaseModel):
    """Fake capability profile model (section ``jira``)."""

    token: SecretStr | None = None
    base_url: str = "https://jira.example.com"
    timeout: float = 30.0


class StrictProfile(BaseModel):
    """Profile model with a required field (section ``strict``)."""

    endpoint: str
    token: SecretStr | None = None


class ExtProfile(BaseModel):
    """Minimal profile model for generic capability doubles."""

    token: str = "default-token"


BUILTIN_REF = ProviderRef(
    kind="built-in",
    distribution="untaped",
    entry_point="",
    api_requires=(1.0, 2.0),
)


def make_spec(
    name: str,
    *,
    section: str | None = None,
    profile_model: type[BaseModel] = ExtProfile,
    state_model: type[BaseModel] | None = None,
    skills: tuple[SkillAsset, ...] = (),
    doctor_checks: tuple[DoctorCheck, ...] = (),
) -> CapabilitySpec:
    """Return a minimal capability spec double mounting an empty sub-app."""

    def _factory() -> App:
        return create_app(name=name, help=f"{name} capability.")

    return CapabilitySpec(
        name=name,
        app_factory=_factory,
        config_section=section or name,
        profile_model=profile_model,
        state_model=state_model,
        skills=skills,
        doctor_checks=doctor_checks,
    )


def registered(
    spec: CapabilitySpec,
    *,
    ref: ProviderRef = BUILTIN_REF,
) -> RegisteredCapability:
    """Wrap ``spec`` as a committed capability."""
    return RegisteredCapability(spec=spec, provider_ref=ref, skills=tuple(spec.skills))


def compose(*specs: CapabilitySpec) -> CompositionResult:
    """Compose ``specs`` as built-ins (registers settings sections)."""
    return bootstrap.compose_root(builtins=specs, externals=())


def write_config(path: Path, text: str) -> None:
    """Write ``text`` to the isolated config file and drop settings caches."""
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def skill_dir(tmp_path: Path, name: str) -> Path:
    """Create a minimal on-disk skill source named ``name``."""
    source = tmp_path / "skill-sources" / name
    source.mkdir(parents=True, exist_ok=True)
    source.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Teach agents how to use {name}.\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    return source


def asset(tmp_path: Path, name: str) -> SkillAsset:
    """Return a skill asset double backed by a real source directory."""
    return SkillAsset(
        name=name,
        source=skill_dir(tmp_path, name),
        description=f"Teach agents how to use {name}.",
    )


def check(
    check_id: str,
    *,
    ok: bool = True,
    detail: str = "all good",
    title: str = "check",
) -> DoctorCheck:
    """Return a doctor-check double reporting a fixed outcome."""

    def _run(_ctx: object) -> DoctorResult:
        return DoctorResult(id=check_id, ok=ok, detail=detail)

    return DoctorCheck(id=check_id, title=title, run=_run)  # type: ignore[arg-type]


__all__ = [
    "BUILTIN_REF",
    "ExtProfile",
    "GithubProfile",
    "GithubState",
    "JiraProfile",
    "StrictProfile",
    "asset",
    "check",
    "compose",
    "make_spec",
    "registered",
    "skill_dir",
    "write_config",
]
