"""awx's server API: the controller's port, how its version is detected, and its adapters.

Commands reach the controller through ``SERVER_API.port(settings)``
(:class:`~untaped_awx.application.ports.AwxApi`); core detects the version on
first use, picks :class:`~untaped_awx.infrastructure.controller.ControllerV2`,
and refuses a controller no adapter serves. ``awx.server_version`` says the
version instead of detecting it.
"""

from __future__ import annotations

from untaped.sdk import key
from untaped.sdk.network import ServerApi
from untaped_awx.application.ports import AwxApi
from untaped_awx.infrastructure.controller import ControllerV2, detect
from untaped_awx.settings import TOKEN, AwxSettings, ControllerVersion

SERVER_API = ServerApi[AwxApi, AwxSettings, ControllerVersion](
    server=key(AwxSettings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(ControllerV2,),
    override=key(AwxSettings).server_version,
)

__all__ = ["SERVER_API"]
