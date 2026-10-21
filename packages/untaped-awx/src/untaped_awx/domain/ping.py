"""Domain entities for the AWX / Ansible Automation Platform context."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from untaped.sdk import Record


class PingStatus(Record, kind="awx.status"):
    """Health status returned by AAP's ``/api/v2/ping/`` endpoint."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str
    active_node: str
    install_uuid: str | None = None
    user: str | None = Field(
        default=None, description="Username the configured token authenticates as (from `/me/`)."
    )
