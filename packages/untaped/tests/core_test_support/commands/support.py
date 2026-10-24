"""Shared doubles for root management-surface tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import pytest
from cyclopts import App
from pydantic import BaseModel, HttpUrl, SecretStr
from rich.console import Console

from untaped.core.skill_stubs import SKILL_FILE, STUB_MARKER, digest
from untaped.runtime import bootstrap
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import CompositionResult, PluginSpec, SkillAsset
from untaped.runtime.settings import active_settings_layout, get_settings
from untaped.sdk import (  # type: ignore[deprecated]
    Credential,
    CredentialKey,
    HttpStatusError,
    NeedsSetup,
    TlsVerify,
    TokenCommand,
    TokenSources,
    online_check,
)
from untaped.sdk._cli import create_app, run_cyclopts_app
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult
from untaped.sdk.marks import Stability, UntapedDeprecationWarning, deprecated
from untaped.sdk.testing import plugin_candidate

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


class WizServer(CredentialKey):
    """The server a ``wiz`` token is for."""

    url: HttpUrl
    verify_tls: TlsVerify = True


class WizProfile(BaseModel):
    """Service double for ``setup`` (section ``wiz``): a server and a declared token."""

    server: Annotated[WizServer | None, NeedsSetup] = None


#: The ``wiz`` token, read last from ``$WIZ_TOKEN``.
WIZ_TOKEN = Credential("wiz", "token", key=WizServer, env=("WIZ_TOKEN",))


class OldWizProfile(WizProfile):
    """``wiz`` with 11.0's ``token`` settings still declared, as github's are until it moves."""

    token: Annotated[SecretStr | None, deprecated(removed_in=12)] = None
    token_command: Annotated[TokenCommand, deprecated(removed_in=12)] = None


class EnvyServer(CredentialKey):
    """The server an ``envy`` token is for."""

    url: HttpUrl


class EnvProfile(BaseModel):
    """Service double with a conventional token variable (section ``envy``)."""

    server: Annotated[EnvyServer | None, NeedsSetup] = None


#: The ``envy`` token, read last from ``$ENVY_TOKEN``.
ENVY_TOKEN = Credential("envy", "token", key=EnvyServer, env=("ENVY_TOKEN",))

#: The credentials :func:`make_spec` declares for a settings double.
CREDENTIALS: dict[type[BaseModel], tuple[Credential[Any], ...]] = {
    WizProfile: (WIZ_TOKEN,),
    EnvProfile: (ENVY_TOKEN,),
}


class UnmarkedWizProfile(BaseModel):
    """An unmarked 11.0 service double, read through the deprecated fallback (section ``wiz``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("WIZ_TOKEN",))  # type: ignore[deprecated]

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
    caches: tuple[Any, ...] = (),
    credentials: tuple[Credential[Any], ...] | None = None,
) -> PluginSpec:
    """Return a minimal plugin spec double mounting an empty sub-app.

    ``credentials`` defaults to what :data:`CREDENTIALS` lists for ``settings``.
    """

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
        caches=caches,
        credentials=CREDENTIALS.get(settings, ()) if credentials is None else credentials,
    )


def compose(*specs: PluginSpec) -> CompositionResult:
    """Compose ``specs`` as providers (registers settings sections)."""
    return bootstrap.compose_root(candidates=[plugin_candidate(spec) for spec in specs])


def root_group(word: str, *specs: PluginSpec) -> Callable[..., None]:
    """The composed root with ``specs``, run as ``untaped <word> <args>``."""
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec) for spec in specs]).meta

    def run(args: list[str], *, console: Console, error_console: Console) -> None:
        run_cyclopts_app(
            root,
            [word, *args],
            console=console,
            error_console=error_console,
            result_action="print_non_int_sys_exit",
        )

    return run


def config_root(*specs: PluginSpec) -> Callable[..., None]:
    """The composed root with ``specs``, run as ``untaped config <args>``."""
    return root_group("config", *specs)


def write_config(path: Path, text: str) -> None:
    """Write ``text`` to the isolated config file and drop settings caches."""
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def written_profile(path: Path, name: str) -> dict[str, Any]:
    """Profile ``name``'s raw values in the config file at ``path`` (the base for ``default``)."""
    return active_settings_layout().profile_data(read_config_dict(path), name) or {}


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
    "CREDENTIALS",
    "ENVY_TOKEN",
    "FAIL",
    "PROBES",
    "WIZ_TOKEN",
    "EnvProfile",
    "ExtProfile",
    "GithubProfile",
    "GithubState",
    "JiraProfile",
    "LegacyProfile",
    "StrictProfile",
    "WizProfile",
    "WizServer",
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
