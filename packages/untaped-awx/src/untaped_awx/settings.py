"""Settings for the AWX plugin: the ``awx`` profile section model."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from untaped.sdk import Cache, NeedsSetup, Secret, code_version, experimental

if TYPE_CHECKING:
    from untaped_awx.domain.lint.resources import AwxResources

#: The ansible.controller / awx.awx collection's token variables, in its order.
_TOKEN_ENV = ("CONTROLLER_OAUTH_TOKEN", "TOWER_OAUTH_TOKEN", "AAP_TOKEN")


class AwxSettings(BaseModel):
    """Connection + behaviour configuration for a single AWX/AAP target."""

    retired_keys: ClassVar[Mapping[str, str]] = {"test_timeout": "test_timeout_seconds"}

    model_config = ConfigDict(frozen=True)

    base_url: Annotated[str | None, NeedsSetup] = Field(
        default=None, description="AWX/AAP URL, for example `https://aap.example.com`."
    )
    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=_TOKEN_ENV)] = Field(
        default=None,
        description=(
            "AWX/AAP API token. Falls back to `token_command`, then "
            "`CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN`."
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


def _resources_version() -> str:
    """The version of what a controller snapshot holds: its model and the code that fills it.

    A snapshot filled by other code is outdated, so an edit to the loader, its
    resolution, its redaction, the export it builds on or the snapshot's model
    (or an upgrade of either distribution) reads the controller again on the
    next run.
    """
    return ":".join(
        (
            code_version(
                "untaped_awx.domain.lint.resources", distributions=("untaped-awx", "untaped-lint")
            ),
            code_version("untaped_awx.application.lint.controller"),
            code_version("untaped_awx.application.lint.resolution"),
            code_version("untaped_awx.domain.lint.secrets"),
            code_version("untaped_awx.application.resources.save_resource"),
            code_version("untaped_awx.application.resources.references"),
        )
    )


def _resources_model() -> type[AwxResources]:
    """The snapshot's model, imported on first use: it loads lint, which ``--help`` never needs."""
    from untaped_awx.domain.lint.resources import AwxResources  # noqa: PLC0415

    return AwxResources


#: The controller snapshot ``awx lint`` reads without FILES: every resource
#: a token can read, per controller, refreshed past an hour. The cache keeps
#: the snapshot itself, keyed by the controller's URL with the token as the
#: partition, so the files are shared by every profile and a token never
#: reads what another token saw.
RESOURCES = Cache(
    "awx",
    "resources",
    label="controller resources",
    max_age=timedelta(hours=1),
    version=_resources_version,
    model=_resources_model,
    split="none",
)


__all__ = ["RESOURCES", "AwxSettings"]
