"""The library's own record: a volume, kind ``library.volume`` (its class name)."""

from __future__ import annotations

from untaped.sdk.contracts import Record


class Volume(Record):
    """A volume as the library knows it; ``shelf`` sees it as a Book."""

    shelf_mark: str
    name: str
    pages: int = 0
