"""Shared doubles for root management-surface tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import pytest
from cyclopts import App
from pydantic import BaseModel, SecretStr

from untaped import bootstrap
from untaped.cli import create_app
from untaped.plugins.registry import (
    CompositionResult,
    DoctorCheck,
    DoctorResult,
    PluginSpec,
    SkillAsset,
)
from untaped.sdk import (
    HttpStatusError,
    NeedsSetup,
    Secret,
    TokenCommand,
    TokenSources,
    online_check,
)
from untaped.settings import get_settings
from untaped.skill_stubs import SKILL_FILE, STUB_MARKER, digest
from untaped.stability import Stability, UntapedDeprecationWarning
from untaped.testing import plugin_candidate

# NOTE: section models are module-level on purpose. The settings registry
# rejects re-registration of a section under a *different* model object, so
# every test reuses these shared classes per section name.


class GithubProfile(BaseModel):
    """Fake plugin settings model (section ``github``)."""

    token: SecretStr | None = None
    base_url: str = "https://api.github.com"
    mode: Literal["on", "off"] | None = None


class GithubState(BaseModel):
    """Fake plugin state model (section ``github``)."""

    cursor: str | None = None


class JiraProfile(BaseModel):
    """Fake plugin settings model (section ``jira``)."""

    token: SecretStr | None = None
    base_url: str = "https://jira.example.com"
    timeout: float = 30.0


class StrictProfile(BaseModel):
    """Settings model with a required field (section ``strict``)."""

    endpoint: str
    token: SecretStr | None = None


class ExtProfile(BaseModel):
    """Minimal settings model for generic plugin doubles."""

    token: str = "default-token"


class WizProfile(BaseModel):
    """Service double for ``setup`` (section ``wiz``); core adds ``token_command``."""

    base_url: Annotated[str | None, NeedsSetup] = None
    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("WIZ_TOKEN",))] = None


class EnvProfile(BaseModel):
    """Service double with a conventional token variable (section ``envy``)."""

    base_url: Annotated[str | None, NeedsSetup] = None
    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("ENVY_TOKEN",))] = None


class UnmarkedWizProfile(BaseModel):
    """An unmarked service double, read through the deprecated fallback (section ``wiz``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("WIZ_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


class LegacyProfile(BaseModel):
    """Unmarked service double without ``token_command`` (section ``legacy``)."""

    base_url: str | None = None
    token: SecretStr | None = None


#: Calls to :func:`wiz_probe`; tests clear it.
PROBES: list[str] = []
#: Non-empty makes :func:`wiz_probe` fail with HTTP 401; tests clear it.
FAIL: list[bool] = []


def wiz_probe() -> str:
    """The ``wiz.api`` online probe: records the call, rejects the token on demand."""
    PROBES.append("probed")
    if FAIL:
        raise HttpStatusError("HTTP 401 from https://wiz/me", status_code=401)
    return "authenticated as alice"


def wiz_api_check() -> DoctorCheck:
    """The ``wiz.api`` online check over :func:`wiz_probe`."""
    return online_check("wiz.api", section="wiz", probe=wiz_probe)


def make_spec(
    name: str,
    *,
    settings: type[BaseModel] = ExtProfile,
    state: type[BaseModel] | None = None,
    skills: tuple[SkillAsset, ...] = (),
    doctor_checks: tuple[DoctorCheck, ...] = (),
    stability: Stability | None = None,
    stores: tuple[Any, ...] = (),
) -> PluginSpec:
    """Return a minimal plugin spec double mounting an empty sub-app."""

    def _factory() -> App:
        return create_app(name=name, help=f"{name} plugin.")

    return PluginSpec(
        name=name,
        app_factory=_factory,
        settings=settings,
        state=state,
        skills=skills,
        doctor_checks=doctor_checks,
        stability=stability,
        stores=stores,
    )


def compose(*specs: PluginSpec) -> CompositionResult:
    """Compose ``specs`` as providers (registers settings sections)."""
    return bootstrap.compose_root(candidates=[plugin_candidate(spec) for spec in specs])


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


def copy_asset(name: str, source: Path, description: str) -> SkillAsset:
    """A copy skill: ``source`` is deprecated (removed in 12.0), so it warns."""
    with pytest.warns(UntapedDeprecationWarning, match=r"SkillAsset\.__init__\(source=\)"):
        return SkillAsset(name=name, source=source, description=description)


def asset(tmp_path: Path, name: str) -> SkillAsset:
    """Return a skill asset double backed by a real source directory."""
    return copy_asset(name, skill_dir(tmp_path, name), f"Teach agents how to use {name}.")


def check(
    check_id: str,
    *,
    ok: bool = True,
    warn: bool = False,
    detail: str = "all good",
    title: str = "check",
) -> DoctorCheck:
    """Return a doctor-check double reporting a fixed outcome."""

    def _run(_ctx: object) -> DoctorResult:
        return DoctorResult(id=check_id, ok=ok, detail=detail, warn=warn)

    return DoctorCheck(id=check_id, title=title, run=_run)  # type: ignore[arg-type]


__all__ = [
    "FAIL",
    "PROBES",
    "EnvProfile",
    "ExtProfile",
    "GithubProfile",
    "GithubState",
    "JiraProfile",
    "LegacyProfile",
    "StrictProfile",
    "WizProfile",
    "asset",
    "check",
    "compose",
    "make_spec",
    "skill_dir",
    "wiz_api_check",
    "wiz_probe",
    "write_config",
]


def age_stub(directory: Path, *, untaped: str = "10.0.0", text: str = "older wording\n") -> None:
    """Make the stub in ``directory`` look written by an older untaped, with other wording."""
    skill = directory / SKILL_FILE
    skill.write_text(text, encoding="utf-8")
    marker = json.loads((directory / STUB_MARKER).read_text(encoding="utf-8"))
    marker["untaped"]["by"] = untaped
    if marker["plugin"] == "untaped":
        marker["plugin_version"] = untaped
    marker["sha256"] = digest({SKILL_FILE: skill.read_bytes()})
    (directory / STUB_MARKER).write_text(json.dumps(marker), encoding="utf-8")
