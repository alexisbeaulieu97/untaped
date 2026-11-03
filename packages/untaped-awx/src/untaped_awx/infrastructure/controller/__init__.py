"""The controller's adapter: detection, and :class:`ControllerV2` for controller API v2.

Only this package builds a controller path: the port
(:class:`~untaped_awx.application.ports.AwxApi`) is all the rest of awx sees.
"""

from __future__ import annotations

from untaped_awx.infrastructure.controller.detection import (
    CONTROLLER_PREFIX,
    GATEWAY_CONTROLLER_PREFIX,
    GATEWAY_PING,
    detect,
)
from untaped_awx.infrastructure.controller.v2 import ControllerV2

__all__ = [
    "CONTROLLER_PREFIX",
    "GATEWAY_CONTROLLER_PREFIX",
    "GATEWAY_PING",
    "ControllerV2",
    "detect",
]
