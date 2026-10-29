"""``PluginSpec.stores``: what composition accepts."""

from __future__ import annotations

import pytest

from untaped.runtime.registry import PluginSpec
from untaped.sdk.errors import ConfigError


def test_stores_holds_only_stores() -> None:
    with pytest.raises(ConfigError, match="not a Store"):
        PluginSpec(name="hello", stores=("data",))  # type: ignore[arg-type]
