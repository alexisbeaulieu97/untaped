"""User-facing configuration loaded from ``~/.untaped/config.yml``.

The YAML schema is profile-based: every configurable value lives inside a
``profiles.<name>`` block. ``profiles.default`` is required and used as the
fallback layer; ``active: <name>`` (or the ``UNTAPED_PROFILE`` env var)
selects the overlay profile. Individual fields can still be overridden with
``UNTAPED_<SECTION>__<FIELD>`` env vars (e.g. ``UNTAPED_AWX__TOKEN``);
precedence is: env > active profile > default profile > schema default.

The ``workspace.workspaces`` registry is **app state** (not user-tunable
config), so it lives at the top level of the YAML and is spliced back into
the merged dict by :class:`ProfilesSettingsSource`.

Override the file path with the ``UNTAPED_CONFIG`` env var.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

from untaped_core.profile_resolver import resolve_profiles

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
    3. fallback to ``"default"``.
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        raw = self._load_raw_yaml(yaml_file)
        active_override = os.environ.get("UNTAPED_PROFILE") or None
        effective, _ = resolve_profiles(raw, active_override=active_override)
        self._splice_workspace_registry(raw, effective)
        super().__init__(settings_cls, effective)

    @staticmethod
    def _load_raw_yaml(yaml_file: Path) -> dict[str, Any]:
        if not yaml_file.is_file():
            return {}
        with yaml_file.open() as f:
            raw = yaml.safe_load(f) or {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _splice_workspace_registry(raw: dict[str, Any], effective: dict[str, Any]) -> None:
        """Hoist top-level ``workspace.workspaces`` (state) into the merged dict.

        Profiles can still set ``workspace.cache_dir``; the registry merges in
        without clobbering other workspace keys.
        """
        ws_state = raw.get("workspace")
        if not isinstance(ws_state, dict):
            return
        registry = ws_state.get("workspaces")
        if registry is None:
            return
        merged_ws = effective.setdefault("workspace", {})
        if isinstance(merged_ws, dict):
            merged_ws["workspaces"] = registry


def resolve_config_path() -> Path:
    """Return the absolute path of the active config file.

    Reads ``UNTAPED_CONFIG`` if set, otherwise falls back to
    ``~/.untaped/config.yml``. The file may or may not exist.
    """
    return Path(os.environ.get("UNTAPED_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the (cached) loaded :class:`Settings` instance."""
    return Settings()
