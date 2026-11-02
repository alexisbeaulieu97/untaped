"""The reader: what a plugin imports from other packages, read from source, importing nothing."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest

from untaped.runtime.plugin_imports import ImportRecord, read_imports


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    yield site
    for name in [
        each for each in sys.modules if each.split(".")[0] in ("untaped_acme", "untaped_host")
    ]:
        del sys.modules[name]


def _write(site: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")


def _read(site: Path, files: dict[str, str]) -> list[tuple[str, int, bool, bool]]:
    _write(site, {"untaped_acme/__init__.py": "", **files})
    records = read_imports("untaped_acme.plugin").records
    return sorted((each.spelled, each.line, each.guarded, each.star) for each in records)


def test_each_form_records_the_name_it_reaches(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": """
                import json
                import untaped.sdk
                import untaped_git.api as git
                from untaped.sdk import human_size, plural as many
                from untaped_shelf.api import *

                def run():
                    from untaped.sdk.storage import Cache
                    return git.RepoStore, untaped.sdk.Plugin, git.RepoStore.for_url
                """,
        },
    )
    assert found == [
        ("untaped.sdk", 2, False, False),
        ("untaped.sdk.Plugin", 9, False, False),
        ("untaped.sdk.human_size", 4, False, False),
        ("untaped.sdk.plural", 4, False, False),
        ("untaped.sdk.storage.Cache", 8, False, False),
        ("untaped_git.api", 3, False, False),
        ("untaped_git.api.RepoStore", 9, False, False),
        ("untaped_shelf.api.*", 5, False, True),
    ]


def test_type_checking_imports_never_run_and_are_not_recorded(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": """
                from typing import TYPE_CHECKING
                import typing

                if TYPE_CHECKING:
                    from untaped.sdk import Gone
                else:
                    from untaped.sdk import Kept
                if typing.TYPE_CHECKING:
                    from untaped.sdk import AlsoGone
                """,
        },
    )
    assert found == [("untaped.sdk.Kept", 7, False, False)]


def test_an_import_the_plugin_falls_back_from_is_guarded(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": """
                import untaped.sdk as sdk

                try:
                    from untaped.sdk import newer
                except ImportError:
                    newer = None
                try:
                    from untaped.sdk import other
                except (ValueError, ModuleNotFoundError):
                    other = None
                try:
                    from untaped.sdk import strict
                except ValueError:
                    strict = None
                if hasattr(sdk, "maybe"):
                    use = sdk.maybe
                """,
        },
    )
    assert found == [
        ("untaped.sdk", 1, False, False),
        ("untaped.sdk.maybe", 16, True, False),
        ("untaped.sdk.newer", 4, True, False),
        ("untaped.sdk.other", 8, True, False),
        ("untaped.sdk.strict", 12, False, False),
    ]


def test_it_follows_the_plugin_s_own_imports_and_reads_nothing_else(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": """
                from untaped_acme import commands
                from .settings import AcmeSettings
                """,
            "untaped_acme/commands/__init__.py": "from . import report\n",
            "untaped_acme/commands/report.py": "from ..words import WORDS\n",
            "untaped_acme/words.py": "from untaped.sdk import plural\n",
            "untaped_acme/settings.py": "from untaped.sdk import Plugin\n",
            "untaped_acme/testing.py": "from untaped.sdk.testing import check\n",
            "untaped_acme/unused.py": "from untaped.sdk import never\n",
        },
    )
    assert found == [
        ("untaped.sdk.Plugin", 1, False, False),
        ("untaped.sdk.plural", 1, False, False),
    ]


def test_a_record_names_its_file_from_the_package_s_folder(site: Path) -> None:
    _write(
        site,
        {
            "untaped_acme/__init__.py": "",
            "untaped_acme/plugin.py": "from untaped_acme.commands import report\n",
            "untaped_acme/commands/__init__.py": "",
            "untaped_acme/commands/report.py": "# a report\nfrom untaped.sdk import plural\n",
        },
    )
    assert read_imports("untaped_acme.plugin").records == (
        ImportRecord(
            module="untaped.sdk", name="plural", file="untaped_acme/commands/report.py", line=2
        ),
    )


def test_a_file_that_does_not_parse_is_skipped(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": "from untaped_acme import broken, fine\n",
            "untaped_acme/broken.py": "def (:\n",
            "untaped_acme/fine.py": "from untaped.sdk import plural\n",
        },
    )
    assert found == [("untaped.sdk.plural", 1, False, False)]


@pytest.mark.parametrize(
    "rebinds",
    [
        "def run(other):\n    return other.size",
        "def run():\n    import json as other\n    return other.size",
        "def run():\n    from untaped_acme import other\n    return other.size",
        "try:\n    pass\nexcept Exception as other:\n    other.size",
        "def other(): ...\nother.size",
        "class other: ...\nother.size",
        "def run[other](x: other) -> other:\n    return other.size",
        "match 1:\n    case {'x': other}:\n        other.size",
        "sizes = [other.size for other in []]",
        "other = None\nother.size",
        "other.size = 1",
    ],
    ids=[
        "parameter",
        "stdlib-import",
        "own-import",
        "except",
        "def",
        "class",
        "type-parameter",
        "match",
        "comprehension",
        "assignment",
        "attribute-store",
    ],
)
def test_attributes_are_read_only_on_a_name_bound_to_one_module(site: Path, rebinds: str) -> None:
    source = f"import untaped_other.api as other\n{rebinds}\n"
    found = _read(site, {"untaped_acme/plugin.py": source})
    assert found == [("untaped_other.api", 1, False, False)]


def test_a_name_bound_to_one_module_is_read_through_attributes(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": """
                import untaped_other.api as other

                def run(x):
                    return other.size(x), [other.kept for _ in x]
                """,
        },
    )
    assert found == [
        ("untaped_other.api", 1, False, False),
        ("untaped_other.api.kept", 4, False, False),
        ("untaped_other.api.size", 4, False, False),
    ]


def test_suppress_and_any_if_guard_and_a_handler_that_only_raises_does_not(site: Path) -> None:
    found = _read(
        site,
        {
            "untaped_acme/plugin.py": """
                import contextlib
                from contextlib import suppress
                from untaped.sdk import VERSION

                with contextlib.suppress(ImportError):
                    from untaped.sdk import newer
                with suppress(ValueError):
                    from untaped.sdk import strict
                if VERSION >= (13,):
                    from untaped.sdk import thirteen
                else:
                    from untaped.sdk import twelve
                try:
                    from untaped.sdk import needed
                except ImportError:
                    raise
                try:
                    from untaped.sdk import logged
                except ImportError as exc:
                    print(exc)
                    raise RuntimeError("needs a newer untaped") from exc

                def load(optional):
                    try:
                        from untaped.sdk import optional_name
                    except ImportError:
                        if optional:
                            return None
                        raise

                def swallow():
                    try:
                        from untaped.sdk import swallowed
                    except ImportError:
                        raise
                    else:
                        from untaped.sdk import otherwise
                    finally:
                        return None
                """,
        },
    )
    assert found == [
        ("untaped.sdk.VERSION", 3, False, False),
        ("untaped.sdk.logged", 18, False, False),
        ("untaped.sdk.needed", 14, False, False),
        ("untaped.sdk.newer", 6, True, False),
        ("untaped.sdk.optional_name", 25, True, False),
        ("untaped.sdk.otherwise", 37, True, False),
        ("untaped.sdk.strict", 8, False, False),
        ("untaped.sdk.swallowed", 33, True, False),
        ("untaped.sdk.thirteen", 10, True, False),
        ("untaped.sdk.twelve", 12, True, False),
    ]


def test_a_plugin_nested_in_another_package_reads_only_its_own_modules(site: Path) -> None:
    _write(
        site,
        {
            "untaped_host/__init__.py": "from untaped.sdk import host_name\n",
            "untaped_host/sdk.py": "from untaped.sdk import host_only\n",
            "untaped_host/plugins/__init__.py": "",
            "untaped_host/plugins/acme/__init__.py": "",
            "untaped_host/plugins/acme/plugin.py": (
                "from untaped_host.sdk import plural\nfrom untaped_host.plugins.acme import words\n"
            ),
            "untaped_host/plugins/acme/words.py": "from untaped_host import sdk\n",
        },
    )
    records = read_imports("untaped_host.plugins.acme.plugin", "untaped_host.plugins.acme").records
    assert sorted((each.spelled, each.file, each.line) for each in records) == [
        ("untaped_host.sdk", "untaped_host/plugins/acme/words.py", 1),
        ("untaped_host.sdk.plural", "untaped_host/plugins/acme/plugin.py", 1),
    ]
