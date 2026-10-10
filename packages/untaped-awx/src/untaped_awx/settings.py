"""Settings for the AWX plugin: the ``awx`` profile section model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from untaped.sdk import TokenCommand, TokenSources, experimental


class AwxSettings(BaseModel):
    """Connection + behaviour configuration for a single AWX/AAP target."""

    # The ansible.controller / awx.awx collection's variables, in its order.
    token_sources: ClassVar[TokenSources] = TokenSources(
        env=("CONTROLLER_OAUTH_TOKEN", "TOWER_OAUTH_TOKEN", "AAP_TOKEN")
    )
    retired_keys: ClassVar[Mapping[str, str]] = {"test_timeout": "test_timeout_seconds"}

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None
    api_prefix: str = "/api/controller/v2/"
    default_organization: str | None = None
    page_size: int = Field(default=200, gt=0)
    test_timeout_seconds: Annotated[float, experimental, Field(gt=0)] = 1800
    test_parallel: Annotated[int, experimental, Field(gt=0)] = 4

    @field_validator("api_prefix")
    @classmethod
    def _api_prefix_shape(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"api_prefix must start with '/' (got {v!r})")
        return v.rstrip("/") + "/"


__all__ = ["AwxSettings"]
