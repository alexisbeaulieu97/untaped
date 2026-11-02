"""Plugins installed as real packages for the tests of what a plugin uses.

Each is an editable install with its metadata on ``sys.path``:
``untaped-shelf`` offers ``BookSource`` and keeps a deprecated
``human_size`` and a deprecated contract method; ``untaped-acme`` uses what
a test writes into its ``report`` module.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

import pytest

from untaped.runtime.registry import PluginCandidate
from untaped.sdk.marks import forget_notices

_SHELF = {
    "untaped_shelf/__init__.py": "",
    "untaped_shelf/plugin.py": '''
        """The ``shelf`` plugin class."""

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Plugin
        from untaped_shelf.api import BookSource


        class ShelfSettings(BaseModel):
            model_config = ConfigDict(frozen=True)


        class Shelf(Plugin):
            """Books."""

            settings = ShelfSettings
            offers = (BookSource,)
        ''',
    "untaped_shelf/api.py": '''
        """What a plugin may import from shelf."""

        import warnings
        from abc import abstractmethod

        from untaped.sdk import Issued, deprecated
        from untaped.sdk.contracts import Contract


        class Book(Issued):
            title: str


        class BookSource(Contract):
            """Where books come from."""

            @abstractmethod
            def all_books(self) -> list[Book]: ...

            @deprecated(removed_in=5, replacement=all_books)
            @warnings.deprecated("BookSource.books is deprecated; use `all_books`")
            def books(self) -> list[Book]:
                raise NotImplementedError


        def size_text(n: int) -> str:
            return str(n)


        @deprecated(removed_in=5, replacement=size_text)
        @warnings.deprecated("human_size is deprecated; use `size_text`")
        def human_size(n: int) -> str:
            return size_text(n)


        __all__ = ["Book", "BookSource", "size_text"]
        ''',
    "untaped_shelf/broken.py": 'raise RuntimeError("shelf is half installed")\n',
}

_ACME_PLUGIN = '''
    """The ``acme`` plugin class."""

    from pydantic import BaseModel, ConfigDict

    from untaped.sdk import Plugin
    from untaped_acme import report


    class AcmeSettings(BaseModel):
        model_config = ConfigDict(frozen=True)


    class Acme(Plugin):
        """Reports."""

        settings = AcmeSettings
        fills = report.FILLS
    '''

FILLS_BOOKS = """
    from untaped.sdk import Record
    from untaped.sdk.contracts import Provider
    from untaped_shelf.api import Book, BookSource


    class Box(Record):
        id: int


    class Shelved(BookSource, Provider):
        def books(self) -> list[Book]:
            return [Book(title="old")]


    FILLS = (Shelved,)
    """

PACKAGES = ("untaped_acme", "untaped_shelf", "untaped_other", "untaped_beta")


class Site:
    """Editable installs under ``root``, each a source folder and its metadata."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.meta = root / "site-packages"
        self.meta.mkdir(parents=True)
        self.monkeypatch = monkeypatch
        monkeypatch.syspath_prepend(str(self.meta))

    def install(
        self,
        distribution: str,
        files: dict[str, str],
        *,
        at: str = "1.0",
        requires: tuple[str, ...] = (),
    ) -> PluginCandidate:
        source = self.root / distribution
        for name, body in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(body).lstrip(), encoding="utf-8")
        self.monkeypatch.syspath_prepend(str(source))
        info = self.meta / f"{distribution.replace('-', '_')}-{at}.dist-info"
        info.mkdir(exist_ok=True)
        lines = [
            f"Name: {distribution}",
            f"Version: {at}",
            *(f"Requires-Dist: {r}" for r in requires),
        ]
        (info / "METADATA").write_text("Metadata-Version: 2.1\n" + "\n".join(lines) + "\n")
        (info / "RECORD").write_text("")
        url = {"url": source.as_uri(), "dir_info": {"editable": True}}
        (info / "direct_url.json").write_text(json.dumps(url))
        package = distribution.replace("-", "_")
        name = distribution.removeprefix("untaped-")
        target = f"{package}.plugin:{name.capitalize()}"
        (info / "entry_points.txt").write_text(f"[untaped.plugins]\n{name} = {target}\n")
        importlib.invalidate_caches()
        return PluginCandidate(
            distribution=distribution,
            name=name,
            target=target,
            distribution_version=at,
            requires_dist=requires,
        )

    def shelf(self, *, at: str = "3.0", requires: tuple[str, ...] = ()) -> PluginCandidate:
        return self.install("untaped-shelf", _SHELF, at=at, requires=requires)

    def acme(self, report: str, *, requires: tuple[str, ...] = ()) -> PluginCandidate:
        if "FILLS" not in report:
            report = f"{dedent(report)}\nFILLS = ()\n"
        files = {
            "untaped_acme/__init__.py": "",
            "untaped_acme/plugin.py": _ACME_PLUGIN,
            "untaped_acme/report.py": report,
        }
        return self.install("untaped-acme", files, requires=requires)


OTHER = '''
    """The ``other`` plugin class."""

    from pydantic import BaseModel, ConfigDict

    from untaped.sdk import Plugin


    class OtherSettings(BaseModel):
        model_config = ConfigDict(frozen=True)


    class Other(Plugin):
        """Other things."""

        settings = OtherSettings


    def later() -> None:
        from untaped_other import later  # noqa: F401 - read, never run
    '''


@contextmanager
def installed_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    """Where a test installs ``shelf``, ``acme`` and ``other``; unloaded after it."""
    forget_notices()
    try:
        yield Site(tmp_path, monkeypatch)
    finally:
        forget_notices()
        for module in [each for each in sys.modules if each.split(".")[0] in PACKAGES]:
            del sys.modules[module]
