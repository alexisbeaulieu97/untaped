"""Shared doubles for root management-surface tests."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Literal, cast

from cyclopts import App
from pydantic import BaseModel, HttpUrl, SecretStr
from rich.console import Console

from core_test_support.runtime.run import hold_run
from untaped.core.skill_stubs import SKILL_FILE, STUB_MARKER, digest
from untaped.runtime import bootstrap
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import CompositionResult, PluginCandidate, PluginSpec, SkillAsset
from untaped.runtime.settings import active_settings_layout, get_settings
from untaped.sdk import (
    Credential,
    CredentialKey,
    HttpStatusError,
    NeedsSetup,
    OnlineCheck,
    Plugin,
    StubSkill,
    TlsVerify,
)
from untaped.sdk._cli import create_app, run_cyclopts_app
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult
from untaped.sdk.marks import Stability
from untaped.sdk.testing import plugin_candidate

# NOTE: section models are module-level on purpose. The settings registry
# rejects re-registration of a section under a *different* model object, so
# every test reuses these shared classes per section name.


class GithubProfile(BaseModel):
    """Fake plugin settings model (section ``github``)."""

    token: SecretStr | None = None
    base_url: str = "https://api.github.com"
    mode: Literal["on", "off"] | None = None


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


def wiz_api() -> OnlineCheck:
    """The plugin class's ``api`` online check over :func:`wiz_probe` (``wiz.api``)."""
    return OnlineCheck(probe=wiz_probe)


def make_spec(
    name: str,
    *,
    settings: type[BaseModel] = ExtProfile,
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
        skills=skills,
        doctor_checks=doctor_checks,
        stability=stability,
        stores=stores,
        caches=caches,
        credentials=CREDENTIALS.get(settings, ()) if credentials is None else credentials,
    )


def _credentials(settings: type[BaseModel]) -> dict[str, Credential[Any]]:
    """The credentials a service double declares, made afresh for each plugin class."""
    if settings is WizProfile:
        return {"token": Credential(key=WizServer, env=("WIZ_TOKEN",))}
    if settings is EnvProfile:
        return {"token": Credential(key=EnvyServer, env=("ENVY_TOKEN",))}
    return {}


def make_plugin(
    name: str,
    *,
    settings: type[BaseModel] = ExtProfile,
    skill: str | None = None,
    stability: Stability | None = None,
    **blocks: object,
) -> type[Plugin]:
    """A plugin class double named ``name``: ``blocks`` are its attributes (``auth=check()``).

    It lives in a package of its own, in ``untaped_double_<name>.plugin``, so
    its blocks know their plugin as an installed plugin's do.
    A service double (:class:`WizProfile`, :class:`EnvProfile`) also holds its
    ``token`` credential.
    """
    held: dict[str, object] = {**_credentials(settings), **blocks}
    # its own top-level package, as an installed plugin has: core finds a block's plugin by it;
    # the class in its leaf ``plugin`` module, and no ``api`` or ``testing`` beside it
    package = ModuleType(f"untaped_double_{name.replace('-', '_')}")
    package.__path__ = []
    module = ModuleType(f"{package.__name__}.plugin")
    package.plugin = module  # type: ignore[attr-defined]
    cls = type(
        name.title().replace("-", ""),
        (Plugin,),
        {
            "__doc__": f"{name} plugin.",
            "__module__": module.__name__,
            "settings": settings,
            "stability": stability,
            "skill": None if skill is None else StubSkill(skill),
            **held,
        },
    )
    setattr(module, cls.__name__, cls)
    sys.modules[package.__name__] = package
    sys.modules[module.__name__] = module
    return cast("type[Plugin]", cls)


def ghost_candidates(*distributions: str) -> tuple[PluginCandidate, ...]:
    """Plugin ``ghost`` from each of ``distributions``: two are quarantined as a duplicate name."""
    ghost = make_plugin("ghost")
    return tuple(
        PluginCandidate(distribution=each, name="ghost", target=ghost) for each in distributions
    )


def compose(*plugins: PluginSpec | type[Plugin]) -> CompositionResult:
    """Compose ``plugins`` (registers settings sections) and hold a run of it (``hold_run``)."""
    result = bootstrap.compose_root(candidates=[plugin_candidate(each) for each in plugins])
    hold_run(result)
    return result


def root_group(
    word: str, *specs: PluginSpec | type[Plugin], also: Sequence[PluginCandidate] = ()
) -> Callable[..., None]:
    """The composed root with ``specs`` and ``also``'s candidates, run as ``untaped <word> …``."""
    candidates = [*(plugin_candidate(spec) for spec in specs), *also]
    root = bootstrap.build_root_app(candidates=candidates).meta

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


def asset(name: str) -> SkillAsset:
    """Return a stub skill asset double named ``name``."""
    return SkillAsset(name=name, description=f"Teach agents how to use {name}.")


def check(
    check_id: str | None = None,
    *,
    ok: bool = True,
    warn: bool = False,
    detail: str = "all good",
    title: str = "check",
) -> DoctorCheck:
    """Return a doctor-check double reporting a fixed outcome.

    Without ``check_id`` it is a plugin class's check, named by its attribute.
    """

    def _run(_ctx: object) -> DoctorResult:
        return DoctorResult(id=check_id, ok=ok, detail=detail, warn=warn)

    if check_id is None:
        return DoctorCheck(title=title, run=_run)  # type: ignore[arg-type]
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
    "JiraProfile",
    "StrictProfile",
    "WizProfile",
    "WizServer",
    "asset",
    "check",
    "compose",
    "ghost_candidates",
    "make_plugin",
    "make_spec",
    "wiz_api",
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
