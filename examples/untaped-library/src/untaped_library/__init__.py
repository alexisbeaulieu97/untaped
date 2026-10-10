"""The example ``library`` plugin: no commands, one setting, and books for ``shelf``.

``provides`` maps the owner's name to a function returning this plugin's
providers; its local import keeps ``untaped.contracts`` out of every run that
asks no contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec
from untaped_library.settings import LibrarySettings

if TYPE_CHECKING:
    from untaped.contracts import Contract

__all__ = ["SPEC"]


def _shelf() -> Sequence[Contract]:
    from untaped_library.adapters.shelf import LibraryBooks  # noqa: PLC0415

    return (LibraryBooks(),)


SPEC = PluginSpec(name="library", settings=LibrarySettings, provides={"shelf": _shelf})
