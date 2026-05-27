"""User-facing configuration loaded from ``~/.untaped/config.yml``.

The YAML schema is profile-based: every configurable value lives inside a
``profiles.<name>`` block. ``profiles.default`` is **optional** — when
present, it serves as a shared overrides layer beneath the active profile;
when absent, the active profile is layered alone. The Pydantic schema
defaults below sit beneath everything. ``active: <name>`` (or the
``UNTAPED_PROFILE`` env var) selects the overlay profile. Individual fields
can still be overridden with ``UNTAPED_<SECTION>__<FIELD>`` env vars
(e.g. ``UNTAPED_AWX__TOKEN``); precedence is:
env > active profile > default profile (if present) > schema default.

The ``workspace.workspaces`` registry is **app state** (not user-tunable
config), so it lives at the top level of the YAML and is spliced back into
the merged dict by :class:`ProfilesSettingsSource`.

Override the file path with the ``UNTAPED_CONFIG`` env var.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

from untaped_core.errors import ConfigError, first_validation_error
from untaped_core.profile_resolver import (
    effective_active_profile_name,
    resolve_profiles,
    splice_workspace_registry,
)

DEFAULT_CONFIG_PATH = "~/.untaped/config.yml"


class HttpSettings(BaseModel):
    """Cross-cutting HTTP behaviour shared by every domain client.

    By default we trust the OS keychain via the ``truststore`` package — this
    handles corporate CAs that have been installed system-wide. Point
    ``ca_bundle`` at a ``.pem`` file to override; set ``verify_ssl: false`` to
    disable verification entirely (last-resort escape hatch — leaves traffic
    open to MITM).
    """

    ca_bundle: Path | None = None
    verify_ssl: bool = True


class AwxSettings(BaseModel):
    """AWX / Ansible Automation Platform settings.

    The default ``api_prefix`` targets AAP's controller endpoint; users
    running upstream AWX should set it to ``/api/v2/``.
    """

    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/api/controller/v2/"
    default_organization: str | None = None
    page_size: int = 200

    @field_validator("api_prefix")
    @classmethod
    def _api_prefix_shape(cls, v: str) -> str:
        if not v.startswith("/") or not v.endswith("/"):
            raise ValueError("api_prefix must start and end with '/'")
        return v


class GithubSettings(BaseModel):
    base_url: str = "https://api.github.com"
    token: SecretStr | None = None


class WorkspaceEntry(BaseModel):
    """One row in the registry that maps a workspace name to its directory.

    Repos are NOT stored here — they live in the per-workspace
    ``untaped.yml`` manifest. Keep this entry small.
    """

    name: str
    path: str


class WorkspaceSettings(BaseModel):
    workspaces: list[WorkspaceEntry] = Field(default_factory=list)
    cache_dir: Path = Field(default=Path("~/.untaped/repositories"))
    """Where bare clones are cached for `git clone --reference`."""
    workspaces_dir: Path = Field(default=Path("~/.untaped/workspaces"))
    """Default parent directory for new workspaces created via `workspace init`."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UNTAPED_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    log_level: str = "INFO"
    http: HttpSettings = Field(default_factory=HttpSettings)
    awx: AwxSettings = Field(default_factory=AwxSettings)
    github: GithubSettings = Field(default_factory=GithubSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        path = resolve_config_path()
        return (
            init_settings,
            env_settings,
            ProfilesSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )


class ProfilesSettingsSource(InitSettingsSource):
    """Pydantic-settings source: parse the YAML, merge profiles, splice
    in the top-level workspace registry, hand the dict to pydantic.

    Active-profile selection precedence:

    1. ``UNTAPED_PROFILE`` env var (per-process override),
    2. ``active:`` key in the YAML,
    3. fallback to ``"default"`` if it exists, otherwise no profile layer
       is applied and schema defaults take over.
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        raw = self._load_raw_yaml(yaml_file)
        effective, _ = resolve_profiles(raw, active_override=effective_active_profile_name(raw))
        splice_workspace_registry(raw, effective)
        super().__init__(settings_cls, effective)

    @staticmethod
    def _load_raw_yaml(yaml_file: Path) -> dict[str, Any]:
        if not yaml_file.is_file():
            return {}
        try:
            with yaml_file.open() as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ConfigError(f"could not parse {yaml_file}: {exc}") from exc
        return raw if isinstance(raw, dict) else {}


def resolve_config_path() -> Path:
    """Return the absolute path of the active config file.

    Reads ``UNTAPED_CONFIG`` if set, otherwise falls back to
    ``~/.untaped/config.yml``. The file may or may not exist.
    """
    return Path(os.environ.get("UNTAPED_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the (cached) loaded :class:`Settings` instance.

    Translates :class:`pydantic.ValidationError` into :class:`ConfigError`
    so a schema mismatch surfaces via ``report_errors`` instead of a
    pydantic traceback. ``YAMLError`` is translated upstream by
    :meth:`ProfilesSettingsSource._load_raw_yaml`.
    """
    # Resolve the path once so the success and error paths can't disagree if
    # UNTAPED_CONFIG were ever flipped between the two reads.
    path = resolve_config_path()
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}: {first_validation_error(exc)}") from exc


def validate_settings_isolated(
    data: dict[str, Any], settings_cls: type[Settings] = Settings
) -> Settings:
    """Validate ``data`` against ``settings_cls`` with the source chain bypassed.

    :class:`pydantic_settings.BaseSettings.model_validate` is **not** a
    pure dict validator — it re-runs the configured source chain (YAML
    file, env vars, file secrets) and overlays ``data`` on top as the
    init source. That's fine when the new value lands in ``init`` (which
    wins), but it silently masks unset-style flows where the caller has
    *removed* a value the file source still holds (because the on-disk
    write hasn't flushed yet). The source chain fills the gap and
    validation passes against stale state.

    This helper builds a one-shot subclass whose
    ``settings_customise_sources`` returns only ``init_settings``, so
    ``data`` is the single input pydantic sees. Same schema, same
    validators, same :class:`pydantic.ValidationError` shape — but
    isolated from disk and env.
    """

    # Six-parameter shape is the pydantic-settings
    # ``settings_customise_sources`` classmethod contract; only
    # ``init_settings`` is consumed here so ``data`` is the single input
    # pydantic sees.
    def _init_only(
        cls: type[Settings],
        settings_cls: type[Settings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)

    validator_cls = cast(
        "type[Settings]",
        type(
            "_ValidateOnly",
            (settings_cls,),
            {"settings_customise_sources": classmethod(_init_only)},
        ),
    )
    return validator_cls.model_validate(data)


# ---------------------------------------------------------------------------
# Federation hook — sketch (not code)
# ---------------------------------------------------------------------------
#
# The Settings class above couples to every domain by name
# (AwxSettings, GithubSettings, WorkspaceSettings) — see
# `packages/untaped-core/AGENTS.md` "Settings schema (intentional
# inversion)" for why this is currently the right shape and when to
# revisit ("the 7th or 8th domain").
#
# When that day comes, the seam looks roughly like this:
#
#     from typing import ClassVar, Protocol, runtime_checkable
#     from importlib.metadata import entry_points
#     from pydantic import create_model
#     from pydantic.fields import FieldInfo
#
#     @runtime_checkable
#     class DomainSettings(Protocol):
#         """Sub-model contributed by a domain package.
#         The model_fields member pins the structural requirement so
#         isinstance checks aren't no-ops (an empty Protocol body
#         matches every object)."""
#         model_fields: ClassVar[dict[str, FieldInfo]]
#
#     def _discover_domain_settings() -> dict[str, type[BaseModel]]:
#         """Walk the ``untaped.domain_settings`` entry-point group.
#         Each entry resolves to a BaseModel subclass keyed by the
#         section name it owns (e.g. ``"awx"`` -> AwxSettings)."""
#         eps = entry_points(group="untaped.domain_settings")
#         return {ep.name: ep.load() for ep in eps}
#
#     # Two-level construction:
#     # 1. Per-domain sub-models (AwxSettings, GithubSettings, …) stay
#     #    plain ``BaseModel`` subclasses owned by their domain package.
#     # 2. The aggregate ``Settings`` is built dynamically via
#     #    ``create_model(__base__=BaseSettings, ...)`` from the discovered
#     #    sub-models. ``__base__=BaseSettings`` (not BaseModel) is
#     #    load-bearing on this *outer* model: it's what makes env-var
#     #    resolution (UNTAPED_<SECTION>__<FIELD>) work. A plain
#     #    create_model on the aggregate silently breaks env-vars.
#     # HttpSettings + WorkspaceSettings stay first-class on the
#     # aggregate because they are cross-cutting rather than
#     # domain-bounded.
#
# Open questions to revisit at federation time:
#   - walk_settings / redact_secrets currently assume static field
#     declaration on Settings; dynamic field creation needs to
#     preserve their introspection path.
#   - config_file.read_profile / write_profile use the static schema;
#     federation must keep their callers stable.
#   - The intentional-inversion defense in AGENTS.md is still load-
#     bearing for the single-schema introspection contract; the
#     federation hook must preserve walk_settings's output shape or
#     update every consumer.
#   - validate_settings_isolated builds a one-shot subclass of the
#     static Settings class. With a dynamically-built aggregate, the
#     "one-shot subclass" trick still works (create_model produces a
#     real class) but the helper's signature needs verification that
#     the dynamic aggregate satisfies ``type[Settings]`` invariants.
