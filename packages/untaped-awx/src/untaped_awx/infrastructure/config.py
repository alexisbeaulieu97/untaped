"""Configuration struct for the AWX/AAP package.

Decouples the package from :class:`untaped_core.Settings`. The only
place that reads ``Settings`` is the CLI composition root, which builds
an :class:`AwxConfig` once and passes it into the AWX adapters.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AwxConfig(BaseModel):
    """Connection + behaviour configuration for a single AWX/AAP target.

    Mirrors the shape of :class:`untaped_core.settings.AwxSettings` so the
    CLI can build one from settings in a single line, but lives in this
    package so adapters can depend on it without importing ``untaped_core``.
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
            raise ValueError("api_prefix must start and end with '/'")
        return v
