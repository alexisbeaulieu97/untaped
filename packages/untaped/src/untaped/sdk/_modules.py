"""The SDK's own modules: ``untaped.sdk`` and its groups, the modules plugins import core from.

Each plugin adds its ``api`` and ``testing`` modules when it has them.
"""

from __future__ import annotations

SDK_MODULES = (
    "untaped.sdk",
    "untaped.sdk.contracts",
    "untaped.sdk.git",
    "untaped.sdk.health",
    "untaped.sdk.network",
    "untaped.sdk.process",
    "untaped.sdk.projects",
    "untaped.sdk.prompt",
    "untaped.sdk.storage",
    "untaped.sdk.testing",
    "untaped.sdk.testing.git",
)
