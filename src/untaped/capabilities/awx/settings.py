"""Settings for the AWX capability: the ``awx`` profile section model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AwxSettings(BaseModel):
    """Connection + behaviour configuration for a single AWX/AAP target."""

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


__all__ = ["AwxSettings"]
