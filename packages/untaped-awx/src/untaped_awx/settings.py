"""Settings for the AWX plugin: the ``awx`` profile section model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from untaped.sdk import experimental, installed_code
from untaped.sdk.health import NeedsSetup
from untaped.sdk.network import Credential, CredentialKey, Release, ServerVersion, TlsVerify
from untaped.sdk.storage import Cache

if TYPE_CHECKING:
    from untaped_awx.domain.lint.resources import Resources

#: The ansible.controller / awx.awx collection's token variables, in its order.
_TOKEN_ENV = ("CONTROLLER_OAUTH_TOKEN", "TOWER_OAUTH_TOKEN", "AAP_TOKEN")


class AwxServer(CredentialKey):
    """The controller a token is for: its URL, and how its certificate is checked."""

    url: HttpUrl = Field(description="AWX/AAP URL, for example `https://aap.example.com`.")
    verify_tls: TlsVerify = Field(
        default=True,
        description=(
            "Check the controller's certificate: `true` (untaped's trust), `false`, "
            "or a CA file for this server alone."
        ),
    )


class Product(StrEnum):
    """The controller's distribution."""

    AAP = "aap"
    AWX = "awx"


#: How each product spells itself in a version's text.
_PRODUCT_NAMES = {Product.AWX: "AWX", Product.AAP: "AAP controller"}


@dataclass(frozen=True)
class ControllerVersion(ServerVersion):
    """An automation controller: ``AWX 24.6.1``, or ``AAP controller 4.6.2``.

    AWX and AAP serve the same controller API v2; AAP 2.5 and later put it
    behind the platform gateway, which :attr:`gateway` derives from the
    version, so the text form says everything.
    """

    product: Product
    release: Release

    @property
    def gateway(self) -> bool:
        """Whether the platform gateway fronts the controller: AAP 2.5, whose controller is 4.6."""
        return self.product is Product.AAP and self.release >= (4, 6)

    def __str__(self) -> str:
        return f"{_PRODUCT_NAMES[self.product]} {self.release}"

    @classmethod
    def parse(cls, text: str) -> Self:
        """The version ``text`` spells: ``AWX 24.6.1`` or ``AAP controller 4.6.2``."""
        spelled = text.strip()
        for product, name in _PRODUCT_NAMES.items():
            if spelled.startswith(name + " "):
                return cls(product, Release.parse(spelled.removeprefix(name + " ")))
        raise ValueError(
            f"a controller version reads `AWX 24.6.1` or `AAP controller 4.6.2`, not {text!r}"
        )


class AwxSettings(BaseModel):
    """Connection + behaviour configuration for a single AWX/AAP target."""

    retired_keys: ClassVar[Mapping[str, str]] = {
        "base_url": "server.url",
    }

    model_config = ConfigDict(frozen=True)

    server: Annotated[AwxServer | None, NeedsSetup] = Field(
        default=None,
        description=(
            "The controller: its `url` and `verify_tls`. Its token is stored with "
            "`untaped auth login awx`."
        ),
    )
    server_version: ControllerVersion | None = Field(
        default=None,
        description=(
            "The controller's version, for example `AWX 24.6.1` or `AAP controller 4.6.2`. "
            "Set, untaped uses it instead of asking the server; leave it unset to detect it."
        ),
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


#: The version of what a controller snapshot holds: its model and the code that fills it.
#: A snapshot filled by other code is outdated, so any change to untaped-awx (its loader,
#: resolution, redaction, kind specs and the export it builds on) or to untaped-lint
#: reads the controller again on the next run.
_RESOURCES_VERSION = installed_code("untaped_awx", "untaped_lint")


def _resources_model() -> type[Resources]:
    """The snapshot's model, imported on first use: it loads lint, which ``--help`` never needs."""
    from untaped_awx.domain.lint.resources import Resources  # noqa: PLC0415

    return Resources


#: The controller snapshot ``awx lint`` reads without FILES: every resource
#: a token can read, per controller, refreshed past an hour. The cache keeps
#: the snapshot itself, keyed by the controller's URL with the token as the
#: partition, so the files are shared by every profile and a token never
#: reads what another token saw.
RESOURCES = Cache(
    label="controller resources",
    max_age=timedelta(hours=1),
    version=_RESOURCES_VERSION,
    model=_resources_model,
    split="none",
)


#: The controller's API token, filed per server by ``untaped auth login awx``;
#: the collection's variables are tried last.
TOKEN = Credential(key=AwxServer, env=_TOKEN_ENV)


__all__ = [
    "RESOURCES",
    "TOKEN",
    "AwxServer",
    "AwxSettings",
    "ControllerVersion",
    "Product",
]
