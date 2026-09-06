"""Settings for the AWX tool.

The connection model lives in :mod:`untaped.capabilities.awx.infrastructure.config`
so adapters can depend on it without importing the capability root;
this module aliases it under the conventional ``<Name>Settings`` name
used by the capability ``SPEC``.
"""

from __future__ import annotations

from untaped.capabilities.awx.infrastructure.config import AwxConfig

AwxSettings = AwxConfig

__all__ = ["AwxConfig", "AwxSettings"]
