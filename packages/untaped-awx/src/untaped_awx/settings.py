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

    base_url: str | None = Field(
        default=None, description="AWX/AAP URL, for example `https://aap.example.com`."
    )
    token: SecretStr | None = Field(
        default=None,
        description=(
            "AWX/AAP API token. Falls back to `token_command`, then "
            "`CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN`."
        ),
    )
    token_command: TokenCommand = Field(
        default=None,
        description=(
            "Command (argv list, no shell) that prints the token; used when `awx.token` is unset."
        ),
    )
    api_prefix: str = Field(
        default="/api/controller/v2/",
        description="API prefix. Standalone AWX usually uses `/api/v2/`.",
    )
    default_organization: str | None = Field(
        default=None,
        description="Organization that scopes name lookups and `apply` documents without one.",
    )
    page_size: int = Field(default=200, gt=0, description="Results requested per AWX API page.")
    test_timeout_seconds: Annotated[
        float,
        experimental,
        Field(
            gt=0,
            description=(
                "Seconds a `test run` case waits before its job is cancelled, unless "
                "`--timeout`, the case's `timeout:` or the suite's `defaults.timeout` "
                "sets it."
            ),
        ),
    ] = 1800
    test_parallel: Annotated[
        int, experimental, Field(gt=0, description="Default `test run --parallel`.")
    ] = 4

    @field_validator("api_prefix")
    @classmethod
    def _api_prefix_shape(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"api_prefix must start with '/' (got {v!r})")
        return v.rstrip("/") + "/"


__all__ = ["AwxSettings"]
