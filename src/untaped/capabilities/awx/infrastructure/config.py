"""Configuration struct for the AWX/AAP package.

Decouples the package from the aggregate settings model. The tool
registers this model as the ``awx`` section, and CLI composition roots
pass it into the AWX adapters.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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
