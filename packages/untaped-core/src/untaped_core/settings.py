"""User-facing configuration loaded from ``~/.untaped/config.yml``.

Override the path with the ``UNTAPED_CONFIG`` env var. Individual fields can be
overridden with ``UNTAPED_<SECTION>__<FIELD>`` env vars (e.g.
``UNTAPED_AWX__TOKEN``); env beats YAML beats defaults.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
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
    base_url: str | None = None
    token: SecretStr | None = None


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
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if path.is_file():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=path))
        sources.append(file_secret_settings)
        return tuple(sources)


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
