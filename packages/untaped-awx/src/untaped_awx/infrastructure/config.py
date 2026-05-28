"""Configuration struct for the AWX/AAP package.

Decouples the package from the aggregate settings model. The plugin
registers this model as the ``awx`` section, and CLI composition roots
pass it into the AWX adapters.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class _AwxSettingsLike(Protocol):
    base_url: str | None
    token: SecretStr | None
    api_prefix: str
    default_organization: str | None
    page_size: int


class _SettingsWithAwx(Protocol):
    awx: _AwxSettingsLike


class AwxConfig(BaseModel):
    """Connection + behaviour configuration for a single AWX/AAP target.

    The model lives in this package so adapters can depend on it without
    importing ``untaped``.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/api/controller/v2/"
    default_organization: str | None = None
    page_size: int = Field(default=200, gt=0)

    @field_validator("api_prefix")
    @classmethod
    def _api_prefix_shape(cls, v: str) -> str:
        if not v.startswith("/") or not v.endswith("/"):
            raise ValueError(f"api_prefix must start and end with '/' (got {v!r})")
        return v

    @classmethod
    def from_settings(cls, settings: _SettingsWithAwx) -> AwxConfig:
        """Build an ``AwxConfig`` from cross-cutting ``Settings``.

        Compatibility bridge for tests and callers that already have an
        aggregate settings object.
        """
        s = settings.awx
        return cls(
            base_url=s.base_url,
            token=s.token,
            api_prefix=s.api_prefix,
            default_organization=s.default_organization,
            page_size=s.page_size,
        )
