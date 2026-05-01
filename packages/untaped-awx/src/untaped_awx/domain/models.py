"""Domain entities for the AWX / Ansible Automation Platform context."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PingStatus(BaseModel):
    """Health status returned by AAP's ``/api/v2/ping/`` endpoint."""

    model_config = ConfigDict(extra="ignore")

    version: str
    active_node: str
    install_uuid: str | None = None
