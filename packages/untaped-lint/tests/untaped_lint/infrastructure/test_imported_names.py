"""The names a module imports from other packages, read from its source."""

from __future__ import annotations

import ast
from textwrap import dedent

import pytest

from untaped_lint.infrastructure.imported_names import imported_names


def _read(
    source: str, module: str = "untaped_acme.plugin", own: str = "untaped_acme"
) -> list[tuple[str, int]]:
    tree = ast.parse(dedent(source).lstrip())
    found = imported_names(tree, module, own=own, is_package=False)
    return sorted(
        {
            (
                f"{each.module}.*"
                if each.star
                else ".".join(filter(None, (each.module, each.name))),
                each.line,
            )
            for each in found
        }
    )


def test_each_form_records_the_name_it_reaches() -> None:
    found = _read(
        """
        import json
        import untaped.sdk
        import untaped_git.api as git
        from untaped.sdk import human_size, plural as many
        from untaped_shelf.api import *

        def run():
            from untaped.sdk.storage import Cache
            try:
                from untaped.sdk import maybe
            except ImportError:
                maybe = None
            return git.RepoStore, untaped.sdk.Plugin, git.RepoStore.for_url
        """
    )
    assert found == [
        ("untaped.sdk", 2),
        ("untaped.sdk.Plugin", 13),
        ("untaped.sdk.human_size", 4),
        ("untaped.sdk.maybe", 10),
        ("untaped.sdk.plural", 4),
        ("untaped.sdk.storage.Cache", 8),
        ("untaped_git.api", 3),
        ("untaped_git.api.RepoStore", 13),
        ("untaped_shelf.api.*", 5),
    ]


def test_type_checking_imports_never_run_and_are_not_recorded() -> None:
    found = _read(
        """
        from typing import TYPE_CHECKING
        import typing

        if TYPE_CHECKING:
            from untaped.sdk import Gone
        else:
            from untaped.sdk import Kept
        if typing.TYPE_CHECKING:
            from untaped.sdk import AlsoGone
        """
    )
    assert found == [("untaped.sdk.Kept", 7)]


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
def test_attributes_are_read_only_on_a_name_bound_to_one_module(rebinds: str) -> None:
    assert _read(f"import untaped_other.api as other\n{rebinds}\n") == [("untaped_other.api", 1)]


def test_a_name_bound_to_one_module_is_read_through_attributes() -> None:
    found = _read(
        """
        import untaped_other.api as other

        def run(x):
            return other.size(x), [other.kept for _ in x]
        """
    )
    assert found == [
        ("untaped_other.api", 1),
        ("untaped_other.api.kept", 4),
        ("untaped_other.api.size", 4),
    ]


def test_relative_imports_and_the_plugins_own_package_are_its_own() -> None:
    source = "from . import words\nfrom .words import plural\nfrom untaped_host.sdk import x\n"
    nested = _read(source, "untaped_host.plugins.acme.plugin", own="untaped_host.plugins.acme")
    assert nested == [("untaped_host.sdk.x", 3)]


def test_a_module_imported_by_from_is_read_through_one_attribute() -> None:
    found = _read("from untaped import sdk\n\nsdk.size_words(3)\n")
    assert found == [("untaped.sdk", 1), ("untaped.sdk.size_words", 3)]


def test_importing_a_submodule_binds_its_parents() -> None:
    found = _read("import untaped.sdk.storage\n\nuntaped.sdk.human_size(3)\n")
    assert found == [("untaped.sdk.human_size", 3), ("untaped.sdk.storage", 1)]


def test_a_function_may_use_a_module_imported_further_down() -> None:
    found = _read(
        """
        def run(x):
            return other.size(x)

        import untaped_other.api as other
        """
    )
    assert found == [("untaped_other.api", 4), ("untaped_other.api.size", 2)]
