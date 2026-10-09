"""The library's own record: a volume, kind ``library.volume``."""

from __future__ import annotations

from untaped.contracts import Record


class Volume(Record, kind="library.volume"):
    """A volume as the library knows it; ``shelf`` sees it as a Book."""

    shelf_mark: str
    name: str
    pages: int = 0
