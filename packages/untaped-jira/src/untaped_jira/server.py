"""Which Jira a server is: its version, and how jira detects it.

:class:`JiraVersion` is what one server runs (``Jira Data Center 11.2.1``,
``Jira Server 9.12.0``, ``Jira Cloud``): its text is how people read it and
how ``jira.server_version`` sets it. :func:`detect` asks the server.
Adapters (:mod:`untaped_jira.data_center`) choose by it;
:mod:`untaped_jira.server_api` declares the block.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, Field

from untaped.sdk.network import Release, ServerVersion

if TYPE_CHECKING:
    from untaped.sdk import HttpStatusError
    from untaped.sdk.network import HttpClient
    from untaped_jira.settings import JiraSettings


class Deployment(StrEnum):
    """How a Jira is run: by Atlassian (Cloud) or by its owner (Data Center, Server)."""

    CLOUD = "cloud"
    DATA_CENTER = "data-center"
    SERVER = "server"


_SELF_HOSTED = {Deployment.DATA_CENTER: "Jira Data Center", Deployment.SERVER: "Jira Server"}


@dataclass(frozen=True)
class JiraVersion(ServerVersion):
    """A Jira: ``Jira Cloud``, ``Jira Data Center 11.2.1`` or ``Jira Server 9.12.0``."""

    deployment: Deployment
    #: The release a self-hosted Jira runs; Cloud reports none worth reading.
    release: Release | None = None

    def __str__(self) -> str:
        if self.deployment is Deployment.CLOUD:
            return "Jira Cloud"
        return f"{_SELF_HOSTED[self.deployment]} {self.release}"

    @classmethod
    def parse(cls, text: str) -> Self:
        """The version ``text`` names; a bare release (``11.2``) is a Data Center one."""
        text = " ".join(text.split())
        if text == "Jira Cloud":
            return cls(Deployment.CLOUD)
        for deployment, name in _SELF_HOSTED.items():
            if text.startswith(f"{name} "):
                return cls(deployment, Release.parse(text.removeprefix(f"{name} ")))
        try:
            return cls(Deployment.DATA_CENTER, Release.parse(text))
        except ValueError:
            raise ValueError(
                f"a Jira version reads like 'Jira Data Center 11.2.1', not {text!r}"
            ) from None


class ServerInfo(BaseModel):
    """The two fields of ``GET /rest/api/2/serverInfo`` detection reads."""

    deployment_type: str = Field(alias="deploymentType")
    version_numbers: Release = Field(alias="versionNumbers")


def is_jira_error(error: HttpStatusError) -> bool:
    """Whether a 404 or 405 is Jira's own answer (it carries ``errorMessages``), not a gone path."""
    return '"errorMessages"' in (error.body or "")


def detect(settings: JiraSettings, client: HttpClient) -> JiraVersion:
    """Asks `GET /rest/api/2/serverInfo`."""
    # The settings model imports this module on every start; the block loads on use.
    from untaped.sdk.network import ApiMismatch  # noqa: PLC0415

    del settings
    with ApiMismatch.watch(vendor_error=is_jira_error):  # a 404 page or another body: not a Jira
        info = ServerInfo.model_validate(client.get_json_dict("/rest/api/2/serverInfo"))
    match info.deployment_type:
        case "Cloud":
            return JiraVersion(Deployment.CLOUD)
        case "Server":
            return JiraVersion(Deployment.SERVER, info.version_numbers)
        case _:
            return JiraVersion(Deployment.DATA_CENTER, info.version_numbers)


__all__ = [
    "Deployment",
    "JiraVersion",
    "ServerInfo",
    "detect",
    "is_jira_error",
]
